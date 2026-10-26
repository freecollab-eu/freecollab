#!/usr/bin/env python3
"""Real native browser WebRTC media, guest admission and screen-share proof.

Uses private API/database/keys, Chromium's synthetic camera/microphone devices,
and the production meeting components in an isolated Vite entry. No existing
service, account, external STUN/TURN server or provider is contacted. An optional
explicit private loopback TURN fixture supports managed Chromium UDP policies.
Native WebRTC and
native media APIs are observed, never replaced by synthetic return objects.
"""
import base64
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('private_meetings_helpers', Path(__file__).with_name('remote-sync-browser.py'))
helpers = importlib.util.module_from_spec(spec); spec.loader.exec_module(helpers)
REPORT = Path(os.getenv('MEETINGS_PROOF_REPORT', str(ROOT.parent / '.freecollab/proof-results/meetings-browser.json')))
SOURCE_FILES = ['backend/app/models/meetings.py','backend/app/models/chat_productivity.py','backend/app/models/message_references.py','backend/app/services/meetings.py','backend/app/api/meetings.py','backend/tests/test_meetings.py','frontend/src/lib/meetings-api.ts','frontend/src/components/MeetingsPanel.tsx','frontend/src/components/MeetingGuest.tsx','frontend/src/styles/meetings.css','frontend/tests/meetings-browser.py','docs/MEETINGS.md']


def create_frontend(private, api_base, app_port):
    web = private / 'web'; web.mkdir()
    (web / 'node_modules').symlink_to(ROOT / 'frontend/node_modules', target_is_directory=True)
    (web / 'index.html').write_text('<!doctype html><html><head><title>FreeCollab meeting proof</title><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><div id="root"></div><script type="module" src="/entry.tsx"></script></body></html>')
    base_css = (ROOT / 'frontend/src/index.css').read_text().split('\n', 2)[2]
    (web / 'base.css').write_text(base_css + '\n.proof-meeting{max-width:1000px;margin:20px auto;padding:24px;background:var(--fc-surface);border:1px solid var(--fc-border);border-radius:18px} @media(max-width:480px){.proof-meeting{padding:14px;margin:10px}}\n')
    source = '''import React from 'react';
import {createRoot} from 'react-dom/client';
import {MeetingsPanel} from COMPONENTS;
import {MeetingGuest} from GUEST;
import {MeetingDock} from DOCK;
import './base.css';
import ACCOUNT;
import CONTENT;
const params=new URLSearchParams(location.hash.slice(1));
const meeting=params.get('meeting'),invitation=params.get('meeting_invite');
if(meeting)history.replaceState(null,'',location.pathname+'#meeting='+encodeURIComponent(meeting));
createRoot(document.getElementById('root')).render(<React.StrictMode>{meeting?<MeetingGuest meetingId={meeting} invitationToken={invitation}/>:<main className="proof-meeting"><h1>Meetings, thoughtfully.</h1><MeetingsPanel workspaceId="team" channelId="shared"/><MeetingDock/></main>}</React.StrictMode>);
'''.replace('COMPONENTS', json.dumps(str(ROOT / 'frontend/src/components/MeetingsPanel.tsx'))).replace('GUEST', json.dumps(str(ROOT / 'frontend/src/components/MeetingGuest.tsx'))).replace('DOCK', json.dumps(str(ROOT / 'frontend/src/components/MeetingDock.tsx'))).replace('ACCOUNT', json.dumps(str(ROOT / 'frontend/src/styles/account.css'))).replace('CONTENT', json.dumps(str(ROOT / 'frontend/src/styles/integration-content.css')))
    (web / 'entry.tsx').write_text(source)
    launcher = private / 'vite.mjs'
    # Match the product's Rust-WASM exclusion. These lazy SDK entry points
    # otherwise trigger a late optimizer reload while a draft is being tested.
    optimizer = {'entries': [str(web / 'entry.tsx')], 'exclude': ['@matrix-org/matrix-sdk-crypto-wasm'],
                 'include': ['matrix-js-sdk', 'matrix-js-sdk/lib/crypto-api',
                             'matrix-js-sdk/lib/crypto-api/CryptoEvent', 'matrix-js-sdk/lib/crypto-api/recovery-key',
                             'matrix-encrypt-attachment', 'yjs', 'socket.io-client']}
    launcher.write_text('import {createServer} from '+json.dumps(str(ROOT/'frontend/node_modules/vite/dist/node/index.js'))+';\nimport react from '+json.dumps(str(ROOT/'frontend/node_modules/@vitejs/plugin-react/dist/index.js'))+';\nconst server=await createServer({configFile:false,root:'+json.dumps(str(web))+',plugins:[react()],cacheDir:'+json.dumps(str(private/'vite-cache'))+',optimizeDeps:'+json.dumps(optimizer)+',resolve:{alias:{react:'+json.dumps(str(ROOT/'frontend/node_modules/react'))+',"react-dom":'+json.dumps(str(ROOT/'frontend/node_modules/react-dom'))+',"lucide-react":'+json.dumps(str(ROOT/'frontend/node_modules/lucide-react'))+'}},server:{host:"127.0.0.1",port:'+str(app_port)+',strictPort:true,fs:{allow:['+json.dumps(str(ROOT/'frontend'))+','+json.dumps(str(web))+','+json.dumps(str(private/'vite-cache'))+']},proxy:{"/api":{target:'+json.dumps(api_base)+',changeOrigin:true,rewrite:p=>p.replace(/^\\/api/,"")}}}});await server.listen();\n')
    return launcher


OBSERVE = '''window.__meetingPCs=[];window.__meetingStreams=[];window.__meetingScreens=[];
const NativePC=window.RTCPeerConnection;window.RTCPeerConnection=class extends NativePC{constructor(...args){super(...args);window.__meetingPCs.push(this)}};
if(navigator.mediaDevices){const media=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);navigator.mediaDevices.getUserMedia=async(...args)=>{const stream=await media(...args);window.__meetingStreams.push(stream);return stream};
if(navigator.mediaDevices.getDisplayMedia){const screen=navigator.mediaDevices.getDisplayMedia.bind(navigator.mediaDevices);navigator.mediaDevices.getDisplayMedia=async(...args)=>{const stream=await screen(...args);window.__meetingScreens.push(stream);return stream}}}
'''


def wait_media(page, peers=1):
    try:
        page.wait_for_function('count=>window.__meetingPCs.filter(pc=>pc.connectionState==="connected").length>=count', arg=peers, timeout=45000)
    except Exception:
        print('NativeRTC sanitized state: '+json.dumps(page.evaluate('''()=>({peers:window.__meetingPCs.map(p=>({connection:p.connectionState,ice:p.iceConnectionState,signalling:p.signalingState,local:!!p.localDescription,remote:!!p.remoteDescription,gathering:p.iceGatheringState,localMedia:p.localDescription?.sdp.split('\\r\\n').filter(l=>/^(m=|a=(send|recv|inactive|group))/.test(l)),remoteMedia:p.remoteDescription?.sdp.split('\\r\\n').filter(l=>/^(m=|a=(send|recv|inactive|group))/.test(l)),transceivers:p.getTransceivers().map(t=>({kind:t.receiver.track.kind,direction:t.direction,current:t.currentDirection,track:t.sender.track?.kind}))})),alerts:[...document.querySelectorAll('[role=alert]')].map(e=>e.textContent),media:window.__meetingStreams.map(s=>s.getTracks().map(t=>({kind:t.kind,state:t.readyState})))})''')),file=sys.stderr)
        raise
    page.wait_for_function('count=>[...document.querySelectorAll("video")].filter(v=>!v.muted&&v.videoWidth>0).length>=count', arg=peers, timeout=30000)
    page.wait_for_function('''async count=>{let receiving=0;for(const pc of window.__meetingPCs){if(pc.connectionState!=='connected')continue;const report=await pc.getStats(),rows=[...report.values()].filter(r=>r.type==='inbound-rtp');if(rows.some(r=>(r.kind==='video'||r.mediaType==='video')&&r.bytesReceived>0&&r.framesDecoded>0)&&rows.some(r=>(r.kind==='audio'||r.mediaType==='audio')&&r.bytesReceived>0))receiving++}return receiving>=count}''',arg=peers,timeout=30000)
    stats = page.evaluate('''async()=>{let bytes=0,frames=0;for(const pc of window.__meetingPCs){const report=await pc.getStats();for(const row of report.values())if(row.type==='inbound-rtp'){bytes+=row.bytesReceived||0;frames+=row.framesDecoded||0}}return{bytes,frames}}''')
    assert stats['bytes'] > 0 and stats['frames'] > 0, 'Native inbound media counters did not advance'


def assert_stopped(page):
    page.wait_for_function('window.__meetingStreams.every(s=>s.getTracks().every(t=>t.readyState==="ended"))', timeout=15000)


def main():
    stage, results, processes, streams = 'private services', {}, [], []
    with tempfile.TemporaryDirectory(prefix='freecollab-meetings-') as directory:
        private = Path(directory)
        try:
            api_port, app_port = helpers.port(), helpers.port(); api_base, app = f'http://127.0.0.1:{api_port}', f'http://127.0.0.1:{app_port}'
            turn_binary = os.getenv('MEETINGS_TEST_TURN_BINARY')
            turn_urls, turn_secret = [], ''
            if turn_binary:
                # A private, explicit operator relay permits a real media proof
                # where managed Chromium forbids non-proxied UDP candidates.
                # Browser policy/proxy settings remain untouched.
                turn_port, turn_secret = helpers.port(), secrets.token_urlsafe(40)
                turn_urls = [f'turn:127.0.0.1:{turn_port}?transport=tcp']
                config = private / 'turn.conf'
                config.write_text('\n'.join(['listening-ip=127.0.0.1','relay-ip=127.0.0.1',f'listening-port={turn_port}','min-port=52000','max-port=52100','realm=freecollab-private-proof','use-auth-secret',f'static-auth-secret={turn_secret}','no-cli','no-tls','no-dtls','no-udp','allow-loopback-peers','no-multicast-peers','relay-threads=1','total-quota=30','user-quota=10',f'pidfile={private / "turn.pid"}',f'log-file={private / "turn-private.log"}'])+'\n');config.chmod(0o600)
                stream=(private/'turn-launch.log').open('w');streams.append(stream);processes.append(subprocess.Popen([turn_binary,'-c',str(config)],env=os.environ.copy(),stdout=stream,stderr=stream,start_new_session=True))
            environment = os.environ.copy(); environment.update(ENVIRONMENT='test',DATABASE_URL='sqlite:///'+str(private/'proof.db'),STORAGE_PATH=str(private/'files'),JWT_SECRET=secrets.token_urlsafe(48),ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(),MESSAGE_ENCRYPTION_KEYS=json.dumps({'proof':base64.b64encode(secrets.token_bytes(32)).decode()}),MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='proof',MATRIX_ENABLED='false',MAIL_DELIVERY_MODE='disabled',OIDC_ENABLED='false',WEBHOOKS_ENABLED='false',OTRUST_ENABLED='false',SYNC_ENABLED='false',PUBLIC_APP_URL=app,CORS_ORIGINS=json.dumps([app]),MEETINGS_ICE_SERVERS='[]',MEETINGS_TURN_URLS='[]',MEETINGS_TURN_SECRET='',PYTHONPATH=str(ROOT/'backend'),PROOF_CREDENTIAL_FILE=str(private/'access.json'))
            environment.update(MEETINGS_TURN_URLS=json.dumps(turn_urls),MEETINGS_TURN_SECRET=turn_secret)
            environment['NO_PROXY'] = ','.join(filter(None,[environment.get('NO_PROXY',''),'localhost','127.0.0.1','::1']))
            python = os.getenv('BACKEND_PYTHON') or (str(ROOT/'backend/.venv/bin/python') if (ROOT/'backend/.venv/bin/python').exists() else sys.executable)
            launcher=private/'api.py'; launcher.write_text('''import json,os
from fastapi import FastAPI
from sqlmodel import Session
from app.api import meetings,auth,clips,sfu,files,secure_messaging,channels,media_capture
from app.database import engine,init_db
from app.models.user import User
from app.models.channel import Channel,ChannelMember
from app.models.workspace import Workspace,WorkspaceMember,WorkspaceChannel
# Typed message references register a foreign key to the scheduling table.
from app.models.chat_productivity import ScheduledMessage
from app.services.auth import create_token
import uvicorn
init_db()
with Session(engine) as session:
    session.add_all([User(id='host',name='Studio host',email='private-host@example.com',password_hash='unused'),User(id='peer',name='Studio peer',email='private-peer@example.com',password_hash='unused')])
    session.add(Workspace(id='team',name='Studio'));session.add(Channel(id='shared',name='Project'));session.commit()
    session.add_all([WorkspaceMember(workspace_id='team',user_id='host',role='owner'),WorkspaceMember(workspace_id='team',user_id='peer'),WorkspaceChannel(channel_id='shared',workspace_id='team'),ChannelMember(channel_id='shared',user_id='host',role='admin'),ChannelMember(channel_id='shared',user_id='peer')]);session.commit()
    result={name:create_token(name,session) for name in ['host','peer']}
    fd=os.open(os.environ['PROOF_CREDENTIAL_FILE'],os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as stream:json.dump(result,stream)
app=FastAPI();app.include_router(meetings.router);app.include_router(auth.router);app.include_router(clips.router);app.include_router(sfu.router);app.include_router(files.router);app.include_router(secure_messaging.router);app.include_router(channels.router);app.include_router(media_capture.router)
@app.get('/ready')
def ready():return{'status':'ready'}
uvicorn.run(app,host='127.0.0.1',port='''+str(api_port)+''',proxy_headers=False,access_log=False)
''')
            for command,filename,cwd in [([python,str(launcher)],'api.log',ROOT/'backend'),(['node',str(create_frontend(private,api_base,app_port))],'vite.log',ROOT/'frontend')]:
                stream=(private/filename).open('w');streams.append(stream);processes.append(subprocess.Popen(command,cwd=cwd,env=environment,stdout=stream,stderr=stream,start_new_session=True))
            helpers.ready(api_base+'/ready',processes);helpers.ready(app+'/',processes)
            tokens=json.loads((private/'access.json').read_text())
            with sync_playwright() as playwright:
                browser_args=['--no-sandbox','--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream','--autoplay-policy=no-user-gesture-required','--auto-select-desktop-capture-source=Entire screen','--allow-http-screen-capture','--enable-usermedia-screen-capturing']
                launch={'headless':True,'args':browser_args}; executable=os.getenv('CHROMIUM_PATH') or shutil.which('chromium')
                if executable:launch['executable_path']=executable
                browser=playwright.chromium.launch(**launch)
                contexts=[]; errors=[]; urls=[]
                def page_for(identity=None):
                    context=browser.new_context(viewport={'width':1400,'height':1000},permissions=['microphone','camera','clipboard-read','clipboard-write']);contexts.append(context)
                    context.add_init_script(OBSERVE)
                    if identity:context.add_init_script('localStorage.setItem("freecollab_token",'+json.dumps(tokens[identity])+');localStorage.setItem("freecollab_user_id",'+json.dumps(identity)+');')
                    page=context.new_page();page.set_default_timeout(30000);page.on('pageerror',lambda e:errors.append(str(e)));page.on('request',lambda r:urls.append(r.url));return page
                try:
                    stage='two native cameras and microphones through production components'
                    host,peer=page_for('host'),page_for('peer');host.goto(app,wait_until='networkidle');peer.goto(app,wait_until='networkidle')
                    try:host.get_by_role('button',name='Start channel meeting',exact=True).click()
                    except Exception:
                        print('Meeting frontend load errors: '+json.dumps(errors),file=sys.stderr)
                        print('Meeting frontend body: '+host.locator('body').inner_text()[:1500],file=sys.stderr)
                        raise
                    expect(host.get_by_role('button',name='End meeting for everyone',exact=True)).to_be_visible()
                    peer.get_by_role('button',name='Refresh meetings',exact=True).click();peer.get_by_role('button',name='Join meeting',exact=True).click()
                    wait_media(host);wait_media(peer)
                    results['actual_native_webrtc_two_way_media_with_inbound_rtp_frames']=True
                    stage='native leave and fresh WebRTC transport on rejoin'
                    peer.get_by_role('button',name='Leave meeting',exact=True).click()
                    host.wait_for_function('window.__meetingPCs.every(p=>p.connectionState==="closed")')
                    peer.get_by_role('button',name='Join meeting',exact=True).click();wait_media(host);wait_media(peer)
                    assert host.evaluate('window.__meetingPCs.length>=2&&window.__meetingPCs[0].connectionState==="closed"')
                    results['native_leave_rejoin_uses_fresh_peer_transport_with_audio_video']=True
                    stage='transient signalling failure then explicit leave and native rejoin'
                    peer.route('**/meeting-access/*/poll*',lambda route:route.fulfill(status=503,content_type='application/json',body='{"detail":"Private proof transient failure"}'),times=1)
                    expect(peer.get_by_role('alert')).to_be_visible();assert_stopped(peer)
                    peer.get_by_role('button',name='Leave meeting',exact=True).click()
                    host.wait_for_function('window.__meetingPCs.every(p=>p.connectionState==="closed")')
                    peer.get_by_role('button',name='Join meeting',exact=True).click();wait_media(host);wait_media(peer)
                    results['transient_signalling_failure_stops_media_explicit_leave_allows_fresh_rejoin']=True
                    stage='real microphone and camera controls'
                    host.get_by_role('button',name='Mute microphone',exact=True).click();assert host.evaluate('window.__meetingStreams[0].getAudioTracks().every(t=>!t.enabled)')
                    host.get_by_role('button',name='Unmute microphone',exact=True).click();host.get_by_role('button',name='Turn camera off',exact=True).click();assert host.evaluate('window.__meetingStreams[0].getVideoTracks().every(t=>!t.enabled)')
                    host.get_by_role('button',name='Turn camera on',exact=True).click();results['native_track_microphone_and_camera_controls']=True
                    stage='actual browser screen capture and camera restoration'
                    host.get_by_role('button',name='Share screen',exact=True).click()
                    expect(host.get_by_role('button',name='Stop sharing screen',exact=True)).to_be_visible(timeout=30000)
                    assert host.evaluate('window.__meetingScreens.length===1&&window.__meetingScreens[0].getVideoTracks().some(t=>t.readyState==="live")')
                    assert host.evaluate('window.__meetingPCs.filter(p=>p.connectionState==="connected").every(p=>p.getTransceivers().find(t=>t.receiver.track.kind==="video")?.sender.track===window.__meetingScreens[0].getVideoTracks()[0])')
                    wait_media(peer);host.get_by_role('button',name='Stop sharing screen',exact=True).click();host.wait_for_function('window.__meetingScreens.every(s=>s.getTracks().every(t=>t.readyState==="ended"))')
                    host.wait_for_function('window.__meetingPCs.filter(p=>p.connectionState==="connected").every(p=>p.getTransceivers().find(t=>t.receiver.track.kind==="video")?.sender.track===window.__meetingStreams[0].getVideoTracks()[0])')
                    wait_media(peer);results['actual_get_display_media_replaces_video_and_stops_on_request']=True
                    stage='guest one-use admission and three-browser mesh'
                    host.get_by_role('button',name='Create guest invitation',exact=True).click();invite=host.get_by_label('One-use guest invitation',exact=True).input_value()
                    visitor=page_for();visitor.goto(invite,wait_until='networkidle');expect(visitor.get_by_label('Your name',exact=True)).to_be_visible();assert 'meeting_invite=' not in visitor.url
                    visitor.get_by_label('Your name',exact=True).fill('External partner');visitor.get_by_role('button',name='Ask to join meeting',exact=True).click();expect(visitor.get_by_role('heading',name='Waiting for the host',exact=True)).to_be_visible()
                    assert visitor.evaluate('window.__meetingStreams.length')==0
                    host.get_by_role('button',name='Admit',exact=True).click();visitor.get_by_role('button',name='Enable camera and microphone',exact=True).click();wait_media(visitor,2);wait_media(host,2);wait_media(peer,2)
                    assert visitor.evaluate('localStorage.getItem("freecollab_token")') is None
                    host.get_by_role('button',name='Hide invitation',exact=True).click();results['guest_fragment_cleared_no_native_account_host_admission_before_capture']=True
                    results['actual_three_browser_mesh_with_guest_media']=True
                    stage='guest refresh requests fresh host admission and WebRTC transport'
                    previous_guest=visitor.evaluate('key=>JSON.parse(sessionStorage.getItem(key)).participant.id','freecollab:meeting:'+invite.split('meeting=')[1].split('&')[0])
                    visitor.reload(wait_until='networkidle');visitor.get_by_role('button',name='Request to rejoin meeting',exact=True).click()
                    expect(visitor.get_by_role('heading',name='Waiting for the host',exact=True)).to_be_visible();assert visitor.evaluate('window.__meetingStreams.length')==0
                    host.get_by_role('button',name='Admit',exact=True).click();visitor.get_by_role('button',name='Enable camera and microphone',exact=True).click()
                    wait_media(visitor,2);wait_media(host,2);wait_media(peer,2)
                    assert visitor.evaluate('key=>JSON.parse(sessionStorage.getItem(key)).participant.id','freecollab:meeting:'+invite.split('meeting=')[1].split('&')[0])!=previous_guest
                    results['guest_refresh_reissues_peer_identity_requires_host_readmission_then_real_media']=True
                    stage='private signalling metadata and encryption status'
                    expect(host.get_by_text('Browser-to-browser encrypted media',exact=True)).to_be_visible()
                    with sqlite3.connect(private/'proof.db') as db:
                        payloads=[row[0] for row in db.execute('SELECT encrypted_payload FROM meetingsignal')]
                        assert payloads and all('v=0' not in value and 'candidate:' not in value for value in payloads)
                        rawhashes=[row[0] for row in db.execute('SELECT credential_hash FROM meetingparticipant')]
                        assert all(value not in tokens.values() for value in rawhashes)
                    for raw in [*tokens.values(),invite.split('meeting_invite=')[1]]:
                        assert all(raw not in url for url in urls)
                    results['signalling_encrypted_at_rest_no_bearer_request_urls_clear_crypto_status']=True
                    REPORT.parent.mkdir(parents=True,exist_ok=True)
                    host.screenshot(path=str(REPORT.with_name('meetings-desktop.png')),full_page=True)
                    host.set_viewport_size({'width':320,'height':900});assert host.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    host.screenshot(path=str(REPORT.with_name('meetings-mobile.png')),full_page=True);host.set_viewport_size({'width':1400,'height':1000});results['meeting_mobile_320px_no_horizontal_overflow']=True
                    stage='guest removal stops media and host end stops remaining device'
                    host.get_by_role('button',name='Remove External partner from meeting',exact=True).click();expect(visitor.get_by_role('alert')).to_be_visible();assert_stopped(visitor)
                    host.get_by_role('button',name='End meeting for everyone',exact=True).click();assert_stopped(host);expect(peer.get_by_role('alert')).to_be_visible();assert_stopped(peer)
                    results['guest_removal_and_host_end_close_native_media_tracks']=True
                    assert not errors,'Browser page errors';results['zero_browser_page_errors']=True
                finally:
                    for context in contexts:context.close()
                    browser.close()
        except Exception:
            print('FAILED meetings proof stage: '+stage,file=sys.stderr)
            if stage == 'private services':
                # Only bounded exception classes and child status escape this
                # disposable fixture; raw logs may contain private capabilities.
                print('Private service exit status: '+json.dumps([{'program':Path(process.args[0]).name,'returncode':process.poll()} for process in processes]),file=sys.stderr)
                for filename in ('api.log','vite.log','turn-launch.log'):
                    path=private/filename
                    if path.is_file():
                        with path.open('rb') as stream:
                            stream.seek(max(0,path.stat().st_size-8192));tail=stream.read(8192).decode(errors='replace')
                        classes=re.findall(r'(?m)^([A-Za-z_][\w.]*(?:Error|Exception)):',tail)
                        classes+=re.findall(r'\b(ERR_[A-Z_0-9]+)\b',tail)
                        print(filename+' exception classes: '+json.dumps(classes[-5:]),file=sys.stderr)
            raise
        finally:
            for process in reversed(processes):
                if process.poll() is None:process.terminate()
            for process in reversed(processes):
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
            for stream in streams:stream.close()
    REPORT.parent.mkdir(parents=True,exist_ok=True);REPORT.write_text(json.dumps({'scope':'Production meeting components with private API/Vite/SQLite and actual Chromium WebRTC; Chromium synthetic camera/microphone/screen devices, no external relay/provider; native fixture sessions; one injected HTTP503 tests recovery, no media API mocks','transport':'Private operator-configured loopback TURN over TCP; managed Chromium non-proxied UDP policy preserved' if turn_binary else 'Native host candidates; no configured relay','results':results,'cleanup':'Own services/relay/database/keys/capabilities/media tracks/browser contexts removed','source_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCE_FILES}},indent=2)+'\n')
    print('Meetings browser proof passed: '+str(len(results))+' checks; actual native WebRTC media and guest admission.')

if __name__=='__main__':main()
