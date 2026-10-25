"""Real trusted-device app disclosure and separately encrypted reply publication.

Called by the owned two-Synapse/TLS fixture. The selected argument intentionally
leaves E2EE only after both approvals; it is not a no-disclosure test. No product
transport, Matrix cryptography, receiver response or native API is mocked.
"""
import json
import hashlib
from pathlib import Path
import sqlite3
import time
from playwright.sync_api import expect


def run_stage(*, pages, api_bases, identities, connections, report_dir, checks,
              received_app_bodies, request, succeeds, database_paths):
    print('Federation proof stage: actual trusted-device foreground app disclosure',flush=True)
    root=Path(__file__).resolve().parents[2]
    sources=['backend/app/api/app_platform.py','backend/app/services/app_platform.py','backend/app/services/app_foreground.py',
        'backend/app/models/app_platform.py','frontend/src/components/AppCommandReview.tsx','frontend/src/lib/installed-app-commands.ts',
        'frontend/tests/installed-app-matrix-stage.py']
    hashes=lambda:{name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in sources}
    before=hashes()
    def checkpoint(status):
        after=hashes()
        (Path(report_dir)/'secure-app-stage-checkpoint.json').write_text(json.dumps({'status':status,
            'scope':'Stage checkpoint only, not final combined fixture proof; no payloads, credentials or provider bodies included',
            'checks':dict(checks),'source_sha256':before,'source_drift':[name for name in sources if before[name]!=after[name]]},indent=2)+'\n')
    checkpoint('starting')
    side='a';page=pages[0];peer=pages[1];channel=connections[side]['channel_id'];actor=identities[side]['member']['user_id']
    selected='  DEVICE SELECTED APP ARGUMENT · Å 🧭\n  preserved indentation  \n';answer='SYNTHETIC PRIVATE APP RESPONSE'
    start=len(received_app_bodies)
    with sqlite3.connect(database_paths[side]) as connection:
        workspace_role=connection.execute('select role from workspacemember where workspace_id=? and user_id=?',(side,actor)).fetchone()[0]
        channel_role=connection.execute('select role from channelmember where channel_id=? and user_id=?',(channel,actor)).fetchone()[0]
        # Explicit fixture setup models the customer's configuring operator on
        # the already verified content device. It creates no new Matrix identity.
        connection.execute('update workspacemember set role=? where workspace_id=? and user_id=?',('admin',side,actor))
        connection.execute('update channelmember set role=? where channel_id=? and user_id=?',('admin',channel,actor));connection.commit()
    passed=False
    try:
        command=page.evaluate('''async({workspace,channel})=>{const {api}=window.fixtureModules;const made=await api.request('/workspaces/'+workspace+'/apps',{method:'POST',body:JSON.stringify({manifest:{app_id:'fixture.secure-device',name:'Owned secure receiver',version:'1.0.0',description:'Reviewed device-only disclosure',secure_foreground:true,scopes:['commands:invoke'],commands:[{name:'review',description:'Only these device-selected arguments',arguments:[{name:'text',label:'Device reviewed text',required:true,max_length:2000}]}]},channel_ids:[channel],endpoint:'https://connect-a.example.com/fixture-app-command',authorization:''})});window.foregroundFixtureInstallation=made.installation.id;const rows=await api.request('/workspaces/'+workspace+'/channels/'+channel+'/apps/commands');return rows.commands.find(row=>row.installation_id===made.installation.id)}''',{'workspace':side,'channel':channel})
        assert command and command['delivery']=='foreground' and command['supports_secure_foreground'] is True
        installation=command['installation_id']
        page.evaluate('''async({workspace,channel,command,text})=>{const {React,createRoot}=window.fixtureModules,{AppCommandReview}=await import('/src/components/AppCommandReview.tsx');const old=document.getElementById('work-proof');window.appFixtureOldDisplay=old.style.display;old.style.display='none';const node=document.createElement('div');node.id='app-foreground-proof';node.style.padding='24px';document.body.append(node);window.appForegroundUI=createRoot(node);window.appForegroundUI.render(React.createElement(AppCommandReview,{workspaceId:workspace,channelId:channel,command,arguments:{text}}))}''',{'workspace':side,'channel':channel,'command':command,'text':selected})
        panel=page.locator('#app-foreground-proof')
        expect(panel.get_by_role('textbox',name='Device reviewed text',exact=True)).to_have_value(selected)
        approve=panel.get_by_role('button',name='Approve and run command',exact=True)
        expect(approve).to_be_disabled();assert len(received_app_bodies)==start
        panel.get_by_role('checkbox',name='I reviewed these exact arguments and approve sharing them with this installed app.',exact=True).check()
        expect(approve).to_be_disabled();assert len(received_app_bodies)==start
        panel.get_by_role('checkbox',name='For this request, I approve these exact arguments leaving end-to-end encryption.',exact=True).check()
        approve.click();expect(panel.get_by_role('status')).to_contain_text('The recipient acknowledged this foreground action.',timeout=90000)
        assert len(received_app_bodies)==start+1
        delivered=json.loads(received_app_bodies[-1]);assert delivered['arguments']=={'text':selected} and delivered['secure_foreground'] is True
        assert not peer.evaluate('''async marker=>(await window.cryptoProof.getMessages(window.proofChannel)).some(row=>row.content?.includes(marker))''',answer)
        checks['actual_trusted_device_requires_both_disclosures_and_relays_only_reviewed_arguments_to_pinned_TLS_app']=True
        for path in database_paths.values():
            with sqlite3.connect(path) as connection:
                dump='\n'.join(connection.iterdump());assert 'DEVICE SELECTED APP ARGUMENT' not in dump and answer not in dump
        with sqlite3.connect(database_paths[side]) as connection:
            assert connection.execute('select count(*) from appjob where installation_id=?',(installation,)).fetchone()[0]==0
            receipt=connection.execute('select status,request_digest from appforegroundreceipt where installation_id=?',(installation,)).fetchone()
            assert receipt[0]=='delivered' and len(receipt[1])==64
        checks['actual_foreground_relay_retains_content_free_receipt_without_native_outbox_argument_or_response_copy']=True
        expect(panel.get_by_role('heading',name='Private response preview',exact=True)).to_be_visible()
        expect(panel.locator('pre')).to_contain_text(answer)
        panel.get_by_role('checkbox',name='I reviewed this response and want to publish it encrypted to this channel.',exact=True).check()
        panel.get_by_role('button',name='Publish encrypted response',exact=True).click()
        expect(panel.get_by_role('button',name='Encrypted response published',exact=True)).to_be_visible(timeout=90000)
        deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            if peer.evaluate('''async marker=>(await window.cryptoProof.getMessages(window.proofChannel)).some(row=>row.content?.includes(marker))''',answer):break
            peer.wait_for_timeout(250)
        else:raise AssertionError('The real peer could not decrypt the separately reviewed encrypted app response')
        for path in database_paths.values():
            with sqlite3.connect(path) as connection:
                dump='\n'.join(connection.iterdump());assert 'DEVICE SELECTED APP ARGUMENT' not in dump and answer not in dump
        checks['actual_app_preview_is_not_auto_published_and_separate_review_publishes_decryptable_Matrix_response']=True
        page.screenshot(path=str(report_dir/'secure-app-foreground.png'))
        passed=True
    finally:
        page.evaluate('''()=>{window.appForegroundUI?.unmount();document.getElementById('app-foreground-proof')?.remove();const node=document.getElementById('work-proof');if(node)node.style.display=window.appFixtureOldDisplay||''}''')
        with sqlite3.connect(database_paths[side]) as connection:
            connection.execute('update workspacemember set role=? where workspace_id=? and user_id=?',(workspace_role,side,actor))
            connection.execute('update channelmember set role=? where channel_id=? and user_id=?',(channel_role,channel,actor));connection.commit()
        checkpoint('passed' if passed else 'failed')
