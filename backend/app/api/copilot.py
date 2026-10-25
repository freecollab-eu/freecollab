"""Opt-in, explicitly scoped production AI; demo endpoints remain separate."""
from datetime import datetime, timedelta
import hashlib
import hmac
import json
import re
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import Field
from sqlalchemy import delete, func, update
from sqlmodel import select

from ..database import get_session
from ..models.account_security import AccountSecurity
from ..models.copilot import CopilotConfig, CopilotConsent, CopilotConversation, CopilotToolApproval, CopilotTurn
from ..models.user import User
from ..services import copilot as service
from ..services.account_security import _secret_key, account_limit, ensure_reauthentication, state_for
from ..services.auth import get_current_user, security
from ..services.request_guard import recheck_actor
from ..services.sso_scope import require_unrestricted
from ..services.workforce_access import require_personal_account
from ..services.workspaces import lock_workspace, require_workspace_member


def no_store(response: Response): response.headers['Cache-Control']='private, no-store'
router=APIRouter(prefix='/workspaces/{workspace_id}/copilot',tags=['copilot'],dependencies=[Depends(no_store)])

class ConfigInput(service.Strict):
    password: str = Field(min_length=1,max_length=72)
    code: str | None = Field(default=None,max_length=64)
    expected_revision: int = Field(ge=0)
    enabled: bool
    endpoint: str = Field(max_length=2048)
    model: str = Field(max_length=100)
    api_key: str | None = Field(default=None,min_length=8,max_length=2048)
    agents: list[service.Agent] = Field(min_length=1,max_length=8)
class ConsentInput(service.Strict):
    enabled: bool
    config_revision: int = Field(ge=1)
class ConversationInput(service.Strict):
    channel_id: str = Field(min_length=1,max_length=128)
class ApprovalInput(service.Strict):
    confirm: bool


def owner(session,workspace,user,data):
    require_unrestricted(session); require_personal_account(session,user.id)
    account_limit(session,'copilot-config',user.id,20,3600); state_for(session,user.id)
    session.execute(update(User).where(User.id==user.id).values(name=User.name))
    session.execute(update(AccountSecurity).where(AccountSecurity.user_id==user.id).values(auth_version=AccountSecurity.auth_version))
    lock_workspace(session,workspace); session.expire_all(); recheck_actor(session,user.id)
    if require_workspace_member(session,workspace,user.id).role!='owner': raise HTTPException(403,'Personal workspace owner access required')
    ensure_reauthentication(session,session.get(User,user.id),data.password,data.code)


@router.get('')
def status(workspace_id: str,user=Depends(get_current_user),session=Depends(get_session)):
    member=require_workspace_member(session,workspace_id,user.id)
    config=service.configuration(session,workspace_id,active=False)
    consent=session.get(CopilotConsent,(workspace_id,user.id))
    fields=service.config_fields(config) if config else None
    conversations=session.exec(select(CopilotConversation).where(CopilotConversation.workspace_id==workspace_id,CopilotConversation.user_id==user.id).order_by(CopilotConversation.created_at.desc()).limit(20)).all()
    visible=[]
    for conversation in conversations:
        try: service.grant(session,workspace_id,conversation.channel_id,user.id)
        except HTTPException: continue
        visible.append({'id':conversation.id,'channel_id':conversation.channel_id,'secure':conversation.secure,'revision':conversation.revision})
    return {'globally_enabled':service.enabled(),'configured':bool(config and config.enabled),'config_revision':config.revision if config else 0,
        'provider':{'endpoint':fields.endpoint,'model':fields.model} if fields else None,'agents':[agent.model_dump() for agent in fields.agents] if fields else [],
        'consented':bool(consent and consent.enabled and config and consent.config_revision==config.revision),'conversations':visible,'role':member.role}


@router.put('/config')
def configure(workspace_id: str,data: ConfigInput,user=Depends(get_current_user),session=Depends(get_session)):
    owner(session,workspace_id,user,data)
    current=service.configuration(session,workspace_id,active=False)
    if data.expected_revision!=(current.revision if current else 0): raise HTTPException(409,'Provider configuration changed; reload it')
    old=service.config_fields(current) if current else None
    if old and data.endpoint!=old.endpoint and not data.api_key:
        raise HTTPException(422,'Enter a new provider key when changing the destination endpoint')
    try:
        provider=service.Provider(endpoint=data.endpoint,model=data.model,api_key=data.api_key or (old.api_key if old else ''),agents=data.agents)
        parsed,_=service.transport.normalize_url(provider.endpoint)
        if parsed.query or not parsed.path or parsed.path=='/': raise ValueError()
        if len({agent.name for agent in provider.agents})!=len(provider.agents) or any(len(set(agent.tools))!=len(agent.tools) or set(agent.tools)-{'create_task','context_sources'} for agent in provider.agents): raise ValueError()
    except Exception: raise HTTPException(422,'Use a public HTTPS443 chat-completions endpoint, a model, a key and unique safe agents') from None
    row=current or CopilotConfig(workspace_id=workspace_id,encrypted_config='',updated_by=user.id,revision=0)
    row.revision+=1; row.enabled=data.enabled; row.updated_by=user.id; row.updated_at=datetime.utcnow()
    row.encrypted_config=service.seal(provider.model_dump(),'config',workspace_id)
    recheck_actor(session,user.id); session.add(row); service.audit(session,workspace_id,user.id,'provider_configured'); session.commit()
    return {'revision':row.revision,'enabled':row.enabled}


@router.put('/consent')
def consent(workspace_id: str,data: ConsentInput,user=Depends(get_current_user),session=Depends(get_session)):
    require_workspace_member(session,workspace_id,user.id)
    # Disabling consent must remain available even if an operator disabled AI.
    lock_workspace(session,workspace_id); recheck_actor(session,user.id)
    config=service.configuration(session,workspace_id,active=data.enabled)
    if not config or data.config_revision!=config.revision: raise HTTPException(409,'Provider configuration changed; review the current destination')
    row=session.get(CopilotConsent,(workspace_id,user.id)) or CopilotConsent(workspace_id=workspace_id,user_id=user.id,config_revision=config.revision)
    row.enabled=data.enabled; row.config_revision=config.revision; row.updated_at=datetime.utcnow(); session.add(row)
    service.audit(session,workspace_id,user.id,'consent_enabled' if data.enabled else 'consent_disabled'); session.commit(); return {'enabled':row.enabled}


@router.post('/conversations',status_code=201)
def create_conversation(workspace_id: str,data: ConversationInput,user=Depends(get_current_user),session=Depends(get_session)):
    account_limit(session,'copilot-conversation',user.id,30,3600)
    secure=service.grant(session,workspace_id,data.channel_id,user.id,lock=True)
    config=service.configuration(session,workspace_id); service.consent_for(session,workspace_id,user.id,config.revision)
    count=session.exec(select(func.count()).select_from(CopilotConversation).where(CopilotConversation.user_id==user.id)).one()
    if count>=20: raise HTTPException(409,'Reset an older conversation before creating another')
    row=CopilotConversation(workspace_id=workspace_id,channel_id=data.channel_id,user_id=user.id,secure=secure)
    session.add(row); session.commit(); return {'id':row.id,'channel_id':row.channel_id,'secure':secure,'revision':0}


@router.get('/conversations/{conversation_id}')
def conversation(workspace_id: str,conversation_id: str,user=Depends(get_current_user),session=Depends(get_session)):
    row=service.conversation_for(session,workspace_id,conversation_id,user.id)
    return {'id':row.id,'channel_id':row.channel_id,'secure':row.secure,'revision':row.revision,'turns':service.history(session,row,user.id)}


@router.delete('/conversations/{conversation_id}',status_code=204)
def reset(workspace_id: str,conversation_id: str,user=Depends(get_current_user),session=Depends(get_session)):
    # Identity ownership permits private cleanup even after channel removal.
    require_workspace_member(session,workspace_id,user.id)
    lock_workspace(session,workspace_id); recheck_actor(session,user.id)
    row=session.get(CopilotConversation,conversation_id,populate_existing=True)
    if not row or row.user_id!=user.id or row.workspace_id!=workspace_id: raise HTTPException(404,'Conversation unavailable')
    turns=select(CopilotTurn.id).where(CopilotTurn.conversation_id==row.id)
    session.execute(delete(CopilotToolApproval).where(CopilotToolApproval.turn_id.in_(turns)))
    session.execute(delete(CopilotTurn).where(CopilotTurn.conversation_id==row.id)); session.delete(row)
    service.audit(session,workspace_id,user.id,'conversation_reset'); session.commit()


@router.get('/conversations/{conversation_id}/turns/{turn_id}/citations')
def citations(workspace_id: str,conversation_id: str,turn_id: str,user=Depends(get_current_user),session=Depends(get_session)):
    row=service.conversation_for(session,workspace_id,conversation_id,user.id)
    turn=session.get(CopilotTurn,turn_id)
    if not turn or turn.conversation_id!=row.id or turn.status!='complete': raise HTTPException(404,'Cited turn unavailable')
    refs=service.opened(turn.encrypted_sources,'sources',turn.id)
    service.check_sources(session,row,refs,user.id)
    return {'citations':refs}


@router.post('/conversations/{conversation_id}/turns')
def send_turn(workspace_id: str,conversation_id: str,data: service.TurnInput,user=Depends(get_current_user),session=Depends(get_session)):
    account_limit(session,'copilot-turn',user.id,30,3600)
    row=service.conversation_for(session,workspace_id,conversation_id,user.id,lock=True)
    config=service.configuration(session,workspace_id)
    if data.expected_config_revision!=config.revision: raise HTTPException(409,'Provider configuration changed; review the current destination before sending')
    service.consent_for(session,workspace_id,user.id,config.revision)
    if not data.disclose_to_provider or row.secure and not data.disclose_decrypted_content: raise HTTPException(422,'Explicitly approve this request and selected decrypted content disclosure')
    digest=hmac.new(_secret_key(),(row.id+service.dump(data.model_dump())).encode(),hashlib.sha256).hexdigest()
    existing=session.exec(select(CopilotTurn).where(CopilotTurn.conversation_id==row.id,CopilotTurn.request_id==data.request_id)).first()
    if existing:
        if existing.request_digest!=digest: raise HTTPException(409,'This request identifier belongs to different content')
        if existing.status=='complete' and existing.encrypted_result:
            service.check_sources(session,row,service.opened(existing.encrypted_sources,'sources',existing.id),user.id)
            return {'id':existing.id,'revision':row.revision,**service.opened(existing.encrypted_result,'result',existing.id)}
        raise HTTPException(409,'This request is processing, failed, or was ephemeral; start a fresh turn')
    if row.revision!=data.expected_revision or row.revision>=service.MAX_HISTORY: raise HTTPException(409,'Conversation changed or reached its turn limit; reload or reset it')
    attempts=session.exec(select(func.count()).select_from(CopilotTurn).where(CopilotTurn.conversation_id==row.id)).one()
    if attempts>=service.MAX_ATTEMPTS: raise HTTPException(409,'Conversation reached its attempt limit; reset it before continuing')
    if sum(len(source.event_ids) for source in data.sources)>8: raise HTTPException(422,'Select at most eight encrypted event references')
    pending=session.exec(select(CopilotTurn).where(CopilotTurn.conversation_id==row.id,CopilotTurn.status=='processing')).first()
    if pending:
        if pending.created_at>datetime.utcnow()-timedelta(minutes=2): raise HTTPException(409,'Wait for the current answer')
        pending.status='failed'; pending.completed_at=datetime.utcnow(); session.add(pending)
    provider=service.config_fields(config)
    match=re.match(r'^@([a-z][a-z0-9_-]{0,31})\b',data.prompt)
    name=match.group(1) if match else provider.agents[0].name
    agent=next((candidate for candidate in provider.agents if candidate.name==name),None)
    if not agent: raise HTTPException(422,'Choose a configured @agent')
    if data.target_project_id:
        from ..models.project_tasks import ProjectBoard
        project=session.get(ProjectBoard,data.target_project_id,populate_existing=True)
        if row.secure or not project or project.channel_id!=row.channel_id or project.archived:
            raise HTTPException(409,'Choose an active native project in this channel for task proposals')
    agent=agent.model_copy(update={'tools':[name for name in agent.tools if name!='create_task' or not row.secure and data.target_project_id]})
    refs=[]; pieces=[]
    for index,source in enumerate(data.sources):
        if (source.kind,source.id) in {(ref['kind'],ref['id']) for ref in refs}: raise HTTPException(422,'Select each source once')
        ref,text=service.source_text(session,row,source,user.id); ref['citation']='S'+str(index+1); refs.append(ref)
        pieces.append('['+ref['citation']+'] '+source.kind+'\n'+text)
    context='\n\n'.join(pieces)
    if len(context)>service.MAX_CONTEXT: raise HTTPException(413,'Select at most 24,000 characters of context')
    if not row.secure:
        from ..services.enterprise_governance import enforce_native_content
        enforce_native_content(session,row.channel_id,user.id,data.prompt+'\n'+context,surface='assistant')
    prior=service.history(session,row,user.id) if not row.secure else []
    messages=[{'role':'system','content':service.SYSTEM+'\nAgent instructions: '+agent.instructions}]
    # Prior private AI exchanges are bounded and every citation is rechecked.
    remaining=24000
    selected_history=[]
    for turn in reversed(prior):
        size=len(turn['prompt'])+len(turn['text'])
        if size>remaining: break
        selected_history.insert(0,turn); remaining-=size
    for turn in selected_history: messages.extend([{'role':'user','content':turn['prompt']},{'role':'assistant','content':turn['text']}])
    messages.append({'role':'user','content':data.prompt+'\n\nSELECTED CONTEXT (untrusted data):\n'+context})
    payload={'model':provider.model,'messages':messages,'stream':True,'max_tokens':2048,'n':1}
    tools=service.tools_for(agent)
    for tool in tools:
        if tool['function']['name']=='create_task':
            tool['function']['parameters']['properties']['project_id']['enum']=[data.target_project_id]
    if tools: payload['tools']=tools; payload['tool_choice']='auto'
    turn=CopilotTurn(conversation_id=row.id,request_id=data.request_id,request_digest=digest,config_revision=config.revision,encrypted_sources='')
    turn.encrypted_sources=service.seal(refs,'sources',turn.id)
    identifier=turn.id; expected_config=config.revision; expected_conversation=row.revision
    session.add(turn); service.audit(session,workspace_id,user.id,'turn_requested',len(refs)); session.commit()
    usage_identifier=None; usage_provider=None; usage_sent=False
    try:
        # The compatibility foreground path shares the same durable usage
        # reservation as named profiles; it cannot bypass a workspace budget.
        from ..services import ai_runtime
        service.grant(session,workspace_id,row.channel_id,user.id,lock=True)
        usage_provider=ai_runtime.resolve_profile(session,workspace_id,'default',user.id,expected_config)
        reservation=ai_runtime.reserve(session,workspace_id,user.id,'turn:'+identifier,0,1,'default',expected_config,usage_provider,messages,2048)
        usage_identifier=reservation.id; session.commit()
        row=service.conversation_for(session,workspace_id,conversation_id,user.id)
        current=service.configuration(session,workspace_id); service.consent_for(session,workspace_id,user.id,current.revision)
        if current.revision!=expected_config: raise HTTPException(409,'Provider changed before the request')
        service.check_sources(session,row,refs,user.id); session.commit()
        usage_sent=True
        answer=service.completion(provider,payload)
        ai_runtime.settle(session,usage_identifier,usage_provider,answer.get('usage')); session.commit()
        row=service.conversation_for(session,workspace_id,conversation_id,user.id,lock=True)
        current=service.configuration(session,workspace_id); service.consent_for(session,workspace_id,user.id,current.revision)
        if current.revision!=expected_config or row.revision!=expected_conversation: raise HTTPException(409,'Provider or conversation changed during the request')
        service.check_sources(session,row,refs,user.id)
        proposals=[]; safe_results=[]
        for call in answer['tool_calls']:
            if call['name'] not in agent.tools: raise HTTPException(502,'Provider proposed an unconfigured tool')
            arguments=json.loads(call['arguments'],object_pairs_hook=service.transport._pairs,parse_constant=service.transport._constant)
            if call['name']=='context_sources':
                if arguments!={}: raise HTTPException(502,'Invalid context tool arguments')
                safe_results.append({'tool':'context_sources','sources':[ref['citation'] for ref in refs]})
            else:
                from ..services.project_tasks import TaskFields
                if not isinstance(arguments,dict) or set(arguments)-{'project_id','title','description'} or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',arguments.get('project_id','')): raise HTTPException(502,'Invalid proposed task')
                if arguments['project_id']!=data.target_project_id: raise HTTPException(502,'Provider proposed a task outside the selected destination')
                fields=TaskFields(title=arguments.get('title',''),description=arguments.get('description',''))
                proposals.append({'id':'p'+str(len(proposals)+1),'tool':'create_task','project_id':arguments['project_id'],'title':fields.title,'description':fields.description})
        if any(int(marker)>len(refs) or int(marker)<1 for marker in re.findall(r'\[S(\d+)\]',answer['text'])): raise HTTPException(502,'Provider cited an unavailable source')
        result={'prompt':data.prompt,'text':answer['text'],'agent':agent.name,'citations':refs,'proposals':proposals,'tool_results':safe_results,'ephemeral':row.secure}
        if not row.secure:
            enforce_native_content(session,row.channel_id,user.id,answer['text'],surface='assistant')
        turn=session.get(CopilotTurn,identifier,populate_existing=True)
        if not turn or turn.status!='processing': raise HTTPException(409,'Conversation was reset or request already finished')
        turn.status='complete'; turn.completed_at=datetime.utcnow()
        if not row.secure: turn.encrypted_result=service.seal(result,'result',turn.id)
        row.revision+=1; session.add(turn); session.add(row); service.audit(session,workspace_id,user.id,'turn_completed',len(refs)); recheck_actor(session,user.id); session.commit()
        return {'id':turn.id,'revision':row.revision,**result}
    except Exception as failure:
        session.rollback()
        if usage_identifier and usage_provider:
            ai_runtime.settle(session,usage_identifier,usage_provider,sent=usage_sent); session.commit()
        failed=session.get(CopilotTurn,identifier,populate_existing=True)
        if failed and failed.status=='processing':
            failed.status='failed'; failed.completed_at=datetime.utcnow(); session.add(failed)
            service.audit(session,workspace_id,user.id,'turn_failed',len(refs)); session.commit()
        if isinstance(failure,HTTPException): raise failure
        raise HTTPException(502,'The provider answer could not be validated; no tool was executed') from None


@router.post('/turns/{turn_id}/tools/{proposal_id}/approve')
def approve(workspace_id: str,turn_id: str,proposal_id: str,data: ApprovalInput,user=Depends(get_current_user),session=Depends(get_session)):
    if not data.confirm: raise HTTPException(422,'Explicit approval required')
    account_limit(session,'copilot-approve',user.id,60,3600)
    turn=session.get(CopilotTurn,turn_id)
    if not turn: raise HTTPException(404,'Proposal unavailable')
    row=service.conversation_for(session,workspace_id,turn.conversation_id,user.id,lock=True)
    config=service.configuration(session,workspace_id); service.consent_for(session,workspace_id,user.id,config.revision)
    if row.secure or turn.config_revision!=config.revision or turn.status!='complete' or not turn.encrypted_result: raise HTTPException(409,'Proposal is no longer available')
    result=service.opened(turn.encrypted_result,'result',turn.id); service.check_sources(session,row,result['citations'],user.id)
    proposal=next((item for item in result['proposals'] if item['id']==proposal_id),None)
    if not proposal: raise HTTPException(404,'Proposal unavailable')
    receipt=session.get(CopilotToolApproval,(turn.id,proposal_id))
    if receipt: return {'task_id':receipt.resource_id,'already_approved':True}
    from ..services.project_tasks import create_workflow_task
    task=create_workflow_task(session,row.channel_id,user.id,proposal['project_id'],proposal['title'],'copilot:'+turn.id+':'+proposal_id,proposal['description'])
    session.add(CopilotToolApproval(turn_id=turn.id,proposal_id=proposal_id,user_id=user.id,resource_id=task.id))
    service.audit(session,workspace_id,user.id,'task_approved'); recheck_actor(session,user.id); session.commit()
    return {'task_id':task.id,'already_approved':False}


# Additive runtime routes share the existing authenticated/no-store prefix.
from .ai_runtime import router as runtime_router
router.include_router(runtime_router)
