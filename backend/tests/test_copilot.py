"""Private production HTTPS/SSE proof and AI disclosure/action boundaries."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import ssl
import threading
from urllib.parse import urlsplit
import pytest
from sqlmodel import Session, select
from app.api import copilot as routes
from app.models.channel import ChannelMember
from app.models.copilot import CopilotConfig,CopilotTurn
from app.models.project_tasks import ProjectBoard,TaskCard
from app.services import copilot as service
from tests.oidc_mock import tls_fixture
from tests.test_data_controls import controls,headers,PASSWORD
PREFIX='/workspaces/a/copilot'
KEY='synthetic-private-provider-key'

@pytest.fixture
def ai(controls,monkeypatch):
    controls[0].app.include_router(routes.router); monkeypatch.setattr(service,'enabled',lambda:True)
    result=controls[0].put(PREFIX+'/config',headers=headers(controls),json={'password':PASSWORD,'expected_revision':0,'enabled':True,'endpoint':'https://ai.example.com/v1/chat/completions','model':'fixture','api_key':KEY,'agents':[{'name':'helper','tools':['create_task','context_sources']}]})
    assert result.status_code==200,result.text
    assert controls[0].put(PREFIX+'/consent',headers=headers(controls),json={'enabled':True,'config_revision':1}).status_code==200
    return controls

def conversation(ai,channel='native'):
    result=ai[0].post(PREFIX+'/conversations',headers=headers(ai),json={'channel_id':channel})
    assert result.status_code==201,result.text
    return result.json()

def ask(ai,row,**changes):
    return ai[0].post(PREFIX+'/conversations/'+row['id']+'/turns',headers=headers(ai),json={'request_id':'private-request-1','expected_revision':row['revision'],'prompt':'@helper summarize','sources':[{'kind':'message','id':'old-root'}],'disclose_to_provider':True,**changes})

@pytest.fixture
def provider(tmp_path,monkeypatch):
    certificate,key=tls_fixture(tmp_path/'tls'); state={'requests':[],'tool':False,'truncate':False}
    class Handler(BaseHTTPRequestHandler):
        protocol_version='HTTP/1.1'
        def log_message(self,*_): pass
        def do_POST(self):
            assert self.headers['Authorization']=='Bearer '+KEY
            state['requests'].append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            delta={'tool_calls':[{'index':0,'id':'call_1','type':'function','function':{'name':'create_task','arguments':json.dumps({'project_id':'project','title':'Approved private task'})}}]} if state['tool'] else {'content':'Private answer [S1]'}
            packet={'choices':[{'index':0,'delta':delta,'finish_reason':'tool_calls' if state['tool'] else 'stop'}]}
            body=(''.join('data: '+json.dumps(frame)+'\r\n\r\n' for frame in state.get('packets',[packet]))+('' if state['truncate'] else 'data: [DONE]\r\n\r\n')).encode()
            self.send_response(200); self.send_header('Content-Type','text/event-stream'); self.send_header('Content-Length',str(len(body))); self.end_headers()
            for index in range(0,len(body),7):self.wfile.write(body[index:index+7]);self.wfile.flush()
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler); context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(certificate,key);server.socket=context.wrap_socket(server.socket,server_side=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();endpoint='https://localhost:'+str(server.server_port)+'/v1/chat/completions'
    normal=service.transport.normalize_url;addresses=service.transport.public_addresses;proxy=service.transport._proxy
    monkeypatch.setenv('SSL_CERT_FILE',certificate)
    monkeypatch.setattr(service.transport,'normalize_url',lambda value,**kw:(urlsplit(value),'localhost') if value==endpoint else normal(value,**kw))
    monkeypatch.setattr(service.transport,'public_addresses',lambda host,**kw:['127.0.0.1'] if host=='localhost' else addresses(host,**kw))
    monkeypatch.setattr(service.transport,'_proxy',lambda host,port:None if host=='localhost' else proxy(host,port))
    try:yield endpoint,state
    finally:server.shutdown();server.server_close();thread.join(timeout=5)

def use_provider(ai,endpoint):
    with Session(ai[1]) as session:
        row=session.get(CopilotConfig,'a');fields=service.config_fields(row);fields.endpoint=endpoint;row.encrypted_config=service.seal(fields.model_dump(),'config','a');session.add(row);session.commit()

def test_real_https_stream_explicit_sources_private_history_encryption_and_reset(ai,provider):
    endpoint,state=provider;use_provider(ai,endpoint);row=conversation(ai);response=ask(ai,row)
    assert response.status_code==200,response.text
    result=response.json();assert result['text']=='Private answer [S1]'
    sent=json.dumps(state['requests']);assert 'OLD PRIVATE CONTENT' in sent and 'HIDDEN MUST STAY PRIVATE' not in sent and 'FRESH PRIVATE CONTENT' not in sent and KEY not in sent
    assert ask(ai,row).json()['id']==result['id'] and len(state['requests'])==1
    with Session(ai[1]) as session:
        assert 'Private answer' not in session.get(CopilotTurn,result['id']).encrypted_result
        assert KEY not in session.get(CopilotConfig,'a').encrypted_config
    assert ai[0].get(PREFIX+'/conversations/'+row['id'],headers=headers(ai)).json()['turns'][0]['text']=='Private answer [S1]'
    assert ai[0].delete(PREFIX+'/conversations/'+row['id'],headers=headers(ai)).status_code==204
    with Session(ai[1]) as session:assert not session.get(CopilotTurn,result['id'])

def test_real_https_tool_only_proposes_until_human_approves_idempotent_task(ai,provider):
    endpoint,state=provider;state['tool']=True;use_provider(ai,endpoint)
    with Session(ai[1]) as session:session.add(ProjectBoard(id='project',channel_id='native',created_by='owner'));session.commit()
    response=ask(ai,conversation(ai),target_project_id='project');assert response.status_code==200,response.text
    result=response.json();assert result['proposals'][0]['title']=='Approved private task'
    task_tool=next(item for item in state['requests'][0]['tools'] if item['function']['name']=='create_task')
    assert task_tool['function']['parameters']['properties']['project_id']['enum']==['project']
    with Session(ai[1]) as session:assert not session.exec(select(TaskCard)).all()
    path=PREFIX+'/turns/'+result['id']+'/tools/p1/approve'
    assert ai[0].post(path,headers=headers(ai),json={'confirm':False}).status_code==422
    approved=ai[0].post(path,headers=headers(ai),json={'confirm':True});assert approved.status_code==200,approved.text
    assert ai[0].post(path,headers=headers(ai),json={'confirm':True}).json()['task_id']==approved.json()['task_id']
    with Session(ai[1]) as session:assert len(session.exec(select(TaskCard)).all())==1

def test_truncated_real_provider_stream_never_publishes_partial_answer(ai,provider):
    endpoint,state=provider;state['truncate']=True;use_provider(ai,endpoint)
    assert ask(ai,conversation(ai)).status_code==502
    with Session(ai[1]) as session:
        turn=session.exec(select(CopilotTurn)).one();assert turn.status=='failed' and turn.encrypted_result is None and turn.completed_at is not None

@pytest.mark.parametrize('ending,truncate,expected',[
    ('stop',False,200),
    ('end_turn',False,502),
    (None,False,502),
    ('stop',True,502),
    ('length',False,502),
    ('unknown_finish',False,502),
],ids=['normal-stop','end-turn-is-not-terminal','missing-terminal','missing-done','truncated-length','unknown-finish'])
def test_real_https_end_turn_is_an_intermediate_marker_not_a_terminal_finish(ai,provider,ending,truncate,expected):
    endpoint,state=provider;state['truncate']=truncate
    state['packets']=[
        {'choices':[{'index':0,'delta':{'content':'Private '},'finish_reason':'end_turn'}]},
        {'choices':[{'index':0,'delta':{'content':'answer [S1]'},'finish_reason':None}]},
        {'choices':[{'index':0,'delta':{},'finish_reason':ending}]},
    ]
    use_provider(ai,endpoint);row=conversation(ai);response=ask(ai,row)
    assert response.status_code==expected,response.text
    assert len(state['requests'])==1 and state['requests'][0]['stream'] is True
    with Session(ai[1]) as session:
        turn=session.exec(select(CopilotTurn)).one()
        if expected==200:
            assert response.json()['text']=='Private answer [S1]' and turn.status=='complete'
            assert service.opened(turn.encrypted_result,'result',turn.id)['text']==response.json()['text']
        else:
            assert turn.status=='failed' and turn.encrypted_result is None and turn.completed_at is not None
            assert ai[0].get(PREFIX+'/conversations/'+row['id'],headers=headers(ai)).json()['turns']==[]

def test_real_https_end_turn_cannot_reopen_an_actually_finished_choice(ai,provider):
    endpoint,state=provider
    state['packets']=[
        {'choices':[{'index':0,'delta':{'content':'Private answer [S1]'},'finish_reason':'stop'}]},
        {'choices':[{'index':0,'delta':{'content':' must not be published'},'finish_reason':'end_turn'}]},
    ]
    use_provider(ai,endpoint);response=ask(ai,conversation(ai))
    assert response.status_code==502
    with Session(ai[1]) as session:
        turn=session.exec(select(CopilotTurn)).one();assert turn.status=='failed' and turn.encrypted_result is None

def test_revoked_membership_after_provider_network_discards_output(ai,monkeypatch):
    def revoke(*_):
        with Session(ai[1]) as session:session.delete(session.get(ChannelMember,('native','owner')));session.commit()
        return {'text':'Never publish [S1]','tool_calls':[]}
    monkeypatch.setattr(service,'completion',revoke);assert ask(ai,conversation(ai)).status_code==403
    with Session(ai[1]) as session:assert session.exec(select(CopilotTurn)).one().encrypted_result is None

def test_secure_device_context_needs_per_request_disclosure_and_has_no_stored_plaintext(ai,monkeypatch):
    seen=[];validated=[]
    monkeypatch.setattr(service,'validate_secure_event',lambda session,channel,event,user:validated.append((channel,event,user)))
    monkeypatch.setattr(service,'completion',lambda provider,payload:seen.append(payload) or {'text':'Ephemeral [S1]','tool_calls':[]})
    row=conversation(ai,'secure');sources=[{'kind':'message','id':'$selected','event_ids':['$selected'],'text':'DEVICE DECRYPTED SECRET'}]
    assert ask(ai,row,sources=sources).status_code==422
    response=ask(ai,row,sources=sources,disclose_decrypted_content=True);assert response.status_code==200,response.text
    assert response.json()['ephemeral'] and 'DEVICE DECRYPTED SECRET' in json.dumps(seen)
    assert validated and all(channel=='secure' for channel,_,_ in validated)
    assert all(tool['function']['name']!='create_task' for tool in seen[0]['tools'])
    with Session(ai[1]) as session:
        turn=session.exec(select(CopilotTurn)).one();assert turn.encrypted_result is None
        assert 'DEVICE DECRYPTED SECRET' not in service.unseal_private(turn.encrypted_sources,'copilot-sources',turn.id)
    assert ai[0].get(PREFIX+'/conversations/'+row['id'],headers=headers(ai)).json()['turns']==[]

def test_consent_private_channels_and_scoped_config_boundaries(ai):
    row=conversation(ai)
    assert ask(ai,row,disclose_to_provider=False).status_code==422
    assert ask(ai,row,sources=[{'kind':'message','id':'hidden-message'}]).status_code==409
    assert ai[0].post(PREFIX+'/conversations',headers=headers(ai),json={'channel_id':'hidden'}).status_code==403
    assert ai[0].put(PREFIX+'/consent',headers=headers(ai),json={'enabled':False,'config_revision':1}).status_code==200
    assert ask(ai,row).status_code==403
    assert ai[0].put(PREFIX+'/config',headers=headers(ai,'scoped'),json={'password':PASSWORD,'expected_revision':1,'enabled':False,'endpoint':'https://ai.example.com/v1/chat/completions','model':'fixture','agents':[{'name':'helper'}]}).status_code==403

@pytest.mark.parametrize('endpoint',['http://ai.example.com/v1/chat/completions','https://127.0.0.1/v1/chat/completions','https://ai.example.com:8443/v1/chat/completions','https://ai.example.com/v1/chat/completions?key=bad'])
def test_provider_rejects_ssrf_and_query_credentials(ai,endpoint):
    assert ai[0].put(PREFIX+'/config',headers=headers(ai),json={'password':PASSWORD,'expected_revision':1,'enabled':True,'endpoint':endpoint,'model':'fixture','agents':[{'name':'helper'}]}).status_code==422

def test_sse_arbitrary_unicode_splits_and_incomplete_or_oversized_frames():
    raw=('data: '+json.dumps({'choices':[{'index':0,'delta':{'content':'Välkommen'},'finish_reason':'stop'}]},ensure_ascii=False)+'\n\ndata: [DONE]\n\n').encode()
    assert service.parse_completion(bytes([byte]) for byte in raw)['text']=='Välkommen'
    with pytest.raises(ValueError):service.parse_completion([raw.replace(b'[DONE]',b'{}')])
    with pytest.raises(ValueError):service.parse_completion([b'data: '+b'x'*service.MAX_RESPONSE])
    with pytest.raises(ValueError):service.parse_completion([raw.replace(b'"stop"',b'"length"')])
    fragments=[{'index':0,'delta':{'tool_calls':[{'index':0,'id':'call','function':{'name':'context_sources','arguments':'{'}}]},'finish_reason':None},
        {'index':0,'delta':{'tool_calls':[{'index':0,'id':None,'function':{'name':None,'arguments':'}'}}]},'finish_reason':'tool_calls'}]
    stream=''.join('data: '+json.dumps({'choices':[choice]})+'\n\n' for choice in fragments)+'data: [DONE]\n\n'
    assert service.parse_completion([stream.encode()])['tool_calls'][0]['arguments']=='{}'


def test_native_file_and_version_bound_client_document_are_explicit_context(ai,monkeypatch):
    from app.models.shared_documents import SharedDocument
    from tests.test_data_controls import FILE_ID
    seen=[]
    monkeypatch.setattr(service,'completion',lambda provider,payload:seen.append(payload) or {'text':'Reviewed [S1] [S2]','tool_calls':[]})
    with Session(ai[1]) as session:session.add(SharedDocument(id='reviewed-doc',channel_id='native',created_by='owner',version=2));session.commit()
    response=ask(ai,conversation(ai),sources=[{'kind':'file','id':FILE_ID},{'kind':'shared_document','id':'reviewed-doc','revision':2,'text':'Explicit verified client document text'}])
    assert response.status_code==200,response.text
    assert 'PRIVATE FILE' in json.dumps(seen) and 'Explicit verified client document text' in json.dumps(seen)
    assert 'NEVER-EXPORT-SERVER-PATH' not in json.dumps(seen)

@pytest.mark.parametrize('change',['consent','message','session'])
def test_provider_return_is_discarded_after_consent_source_or_session_changes(ai,monkeypatch,change):
    from app.models.copilot import CopilotConsent
    from app.models.message import Message
    from app.models.session import ActiveSession
    from app.services.crypto import encrypt_message
    def mutate(*_):
        with Session(ai[1]) as session:
            if change=='consent':
                row=session.get(CopilotConsent,('a','owner'));row.enabled=False
            elif change=='message':
                row=session.get(Message,'old-root');row.content=encrypt_message('Changed after explicit review','native')
            else:
                from app.services.auth import token_digest
                row=session.exec(select(ActiveSession).where(ActiveSession.token_hash==token_digest(ai[2]['owner']))).one();row.revoked_at=datetime.utcnow()
            session.add(row);session.commit()
        return {'text':'Must not publish [S1]','tool_calls':[]}
    from datetime import datetime
    monkeypatch.setattr(service,'completion',mutate)
    response=ask(ai,conversation(ai));assert response.status_code in {401,403,409},response.text
    with Session(ai[1]) as session:assert session.exec(select(CopilotTurn)).one().encrypted_result is None


def test_failed_attempt_limit_is_bounded_and_secure_citations_revalidate_current_grants(ai,monkeypatch):
    from fastapi import HTTPException
    row=conversation(ai)
    with Session(ai[1]) as session:
        session.add(CopilotTurn(conversation_id=row['id'],request_id='failed-old-request',request_digest='opaque',config_revision=1,status='failed',encrypted_sources=service.seal([],'sources','unused')));session.commit()
    monkeypatch.setattr(service,'MAX_ATTEMPTS',1)
    assert ask(ai,row).status_code==409
    assert ai[0].get(PREFIX+'/conversations/'+row['id'],headers=headers(ai)).json()['turns']==[]
    monkeypatch.setattr(service,'MAX_ATTEMPTS',24)
    monkeypatch.setattr(service,'validate_secure_event',lambda *_:None)
    monkeypatch.setattr(service,'completion',lambda *_:{'text':'Ephemeral [S1]','tool_calls':[]})
    secure=conversation(ai,'secure');result=ask(ai,secure,sources=[{'kind':'message','id':'$selected','event_ids':['$selected'],'text':'Explicit device context'}],disclose_decrypted_content=True).json()
    path=PREFIX+'/conversations/'+secure['id']+'/turns/'+result['id']+'/citations'
    assert ai[0].get(path,headers=headers(ai)).status_code==200
    def revoked(*_):raise HTTPException(403,'Fixture secure event no longer authorized')
    monkeypatch.setattr(service,'validate_secure_event',revoked)
    assert ai[0].get(path,headers=headers(ai)).status_code==403


def test_endpoint_change_cannot_forward_an_existing_hidden_key_to_a_new_destination(ai):
    data={'password':PASSWORD,'expected_revision':1,'enabled':True,'endpoint':'https://different-ai.example.com/v1/chat/completions','model':'fixture','agents':[{'name':'helper'}]}
    assert ai[0].put(PREFIX+'/config',headers=headers(ai),json=data).status_code==422
    with Session(ai[1]) as session:assert service.config_fields(session.get(CopilotConfig,'a')).endpoint=='https://ai.example.com/v1/chat/completions'
    data['endpoint']='https://ai.example.com/v1/chat/completions'
    assert ai[0].put(PREFIX+'/config',headers=headers(ai),json=data).status_code==200
    assert ai[0].post(PREFIX+'/conversations',headers=headers(ai),json={'channel_id':'native'}).status_code==403
    assert ai[0].put(PREFIX+'/consent',headers=headers(ai),json={'enabled':True,'config_revision':2}).status_code==200
    assert ai[0].post(PREFIX+'/conversations',headers=headers(ai),json={'channel_id':'native'}).status_code==201


def test_task_destination_is_current_native_and_selected_not_an_arbitrary_project(ai,monkeypatch):
    with Session(ai[1]) as session:
        session.add(ProjectBoard(id='other-project',channel_id='other',created_by='outsider'))
        session.add(ProjectBoard(id='archived-project',channel_id='native',created_by='owner',archived=True));session.commit()
    row=conversation(ai)
    assert ask(ai,row,target_project_id='other-project').status_code==409
    assert ask(ai,row,target_project_id='archived-project').status_code==409
    monkeypatch.setattr(service,'completion',lambda *_:{'text':'','tool_calls':[{'name':'create_task','arguments':json.dumps({'project_id':'other-project','title':'Unselected task'})}]})
    assert ask(ai,row).status_code==502
    with Session(ai[1]) as session:assert not session.exec(select(TaskCard)).all()
