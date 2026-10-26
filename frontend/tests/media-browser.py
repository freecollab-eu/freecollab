#!/usr/bin/env python3
"""Actual Chromium MediaRecorder/playback, persistent dock and optional real LiveKit.

Uses only disposable local accounts, keys, SQLite, Vite and a caller-provided
LiveKit binary. Native browser capture/codec/transport APIs are never mocked.
Set LIVEKIT_TEST_BINARY to run actual seven-device SFU publication/subscription.
Set CLIPS_TEST_LOCAL_ASR=1 to download/run the real pinned Whisper model too.
"""
import base64
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('media_meeting_harness', Path(__file__).with_name('meetings-browser.py'))
harness = importlib.util.module_from_spec(spec); spec.loader.exec_module(harness)
helpers = harness.helpers
REPORT = Path(os.getenv('MEDIA_PROOF_REPORT', str(ROOT.parent / '.freecollab/proof-results' / ('media-sfu-browser.json' if os.getenv('LIVEKIT_TEST_BINARY') else 'media-browser.json'))))


def main():
    processes, streams, results, stage = [], [], {}, 'private services'
    binary = os.getenv('LIVEKIT_TEST_BINARY')
    with tempfile.TemporaryDirectory(prefix='freecollab-media-') as directory:
        private = Path(directory)
        try:
            api_port, app_port, sfu_port, rtc_port = [helpers.port() for _ in range(4)]
            api_base, app = f'http://127.0.0.1:{api_port}', f'http://127.0.0.1:{app_port}'
            key, secret = 'fixture-' + secrets.token_hex(8), secrets.token_urlsafe(40)
            env = os.environ.copy(); env.update(STORAGE_BACKEND='local', ENVIRONMENT='test', DATABASE_URL='sqlite:///' + str(private/'proof.db'), STORAGE_PATH=str(private/'files'), JWT_SECRET=secrets.token_urlsafe(48), ACCOUNT_SECURITY_KEY=base64.b64encode(secrets.token_bytes(32)).decode(), MESSAGE_ENCRYPTION_KEYS=json.dumps({'proof': base64.b64encode(secrets.token_bytes(32)).decode()}), MESSAGE_ENCRYPTION_ACTIVE_KEY_ID='proof', MATRIX_ENABLED='false', MAIL_DELIVERY_MODE='disabled', OIDC_ENABLED='false', WEBHOOKS_ENABLED='false', OTRUST_ENABLED='false', SYNC_ENABLED='false', PUBLIC_APP_URL=app, CORS_ORIGINS=json.dumps([app]), PYTHONPATH=str(ROOT/'backend'), PROOF_CREDENTIAL_FILE=str(private/'access.json'), PROOF_API_PORT=str(api_port), MEETINGS_SFU_PROVIDER='livekit' if binary else 'disabled', MEETINGS_SFU_URL=f'ws://127.0.0.1:{sfu_port}', MEETINGS_SFU_API_URL=f'http://127.0.0.1:{sfu_port}', MEETINGS_SFU_API_KEY=key, MEETINGS_SFU_API_SECRET=secret, MEETINGS_SFU_MAX_PARTICIPANTS='100', MEETINGS_ICE_SERVERS='[]', MEETINGS_TURN_URLS='[]', MEETINGS_TURN_SECRET='')
            env['NO_PROXY'] = ','.join(filter(None, [env.get('NO_PROXY', ''), 'localhost', '127.0.0.1', '::1']))
            virtual_python = ROOT/'backend/.venv/bin/python'
            python = os.getenv('BACKEND_PYTHON') or (str(virtual_python) if virtual_python.exists() else sys.executable)
            commands = []
            if binary:
                config = private/'livekit.yaml'; config.write_text(f'port: {sfu_port}\nbind_addresses: [127.0.0.1]\nkeys:\n  {key}: {secret}\nrtc:\n  tcp_port: {rtc_port}\n  force_tcp: true\n  enable_loopback_candidate: true\n  use_external_ip: false\nlogging:\n  level: error\n'); config.chmod(0o600)
                commands.append(([binary, '--config', str(config), '--node-ip', '127.0.0.1'], 'livekit.log', ROOT))
            launcher = harness.create_frontend(private, api_base, app_port)
            mirror = os.getenv('CLIPS_TEST_MODEL_DIRECTORY')
            if mirror:
                destination = private/'web/public/whisper-models'; destination.parent.mkdir(parents=True, exist_ok=True); destination.symlink_to(Path(mirror).resolve(), target_is_directory=True)
                env['VITE_WHISPER_MODEL_BASE_PATH'] = '/whisper-models/'
                launcher.write_text(launcher.read_text().replace('fs:{allow:[', 'fs:{allow:[' + json.dumps(str(Path(mirror).resolve())) + ','))
            launcher.write_text(launcher.read_text().replace('strictPort:true,fs:', 'strictPort:true,hmr:false,watch:{ignored:["**"]},fs:'))
            entry = private/'web/entry.tsx'; source = entry.read_text()
            studio = '''function Studio(){const[visible,setVisible]=React.useState(true);window.proofSetTools=setVisible;return <main className="proof-meeting"><h1>Meetings, thoughtfully.</h1>{visible&&<MeetingsPanel workspaceId="team" channelId="shared"/>}<MeetingDock/></main>};\n'''
            source = source.replace('const params=', studio + 'const params=')
            source = source.replace('<main className="proof-meeting"><h1>Meetings, thoughtfully.</h1><MeetingsPanel workspaceId="team" channelId="shared"/><MeetingDock/></main>', '<Studio/>')
            entry.write_text(source)
            commands += [([python, str(ROOT/'backend/tests/media_browser_api.py')], 'api.log', ROOT/'backend'), (['node', str(launcher)], 'vite.log', ROOT/'frontend')]
            for command, name, cwd in commands:
                stream = (private/name).open('w'); streams.append(stream); processes.append(subprocess.Popen(command, cwd=cwd, env=env, stdout=stream, stderr=stream, start_new_session=True))
            helpers.ready(api_base + '/ready', processes); harness.ready_frontend(app, processes)
            if binary:
                stream = (private/'worker.log').open('w'); streams.append(stream); processes.append(subprocess.Popen([python, '-m', 'app.services.sfu_worker'], cwd=ROOT/'backend', env=env, stdout=stream, stderr=stream, start_new_session=True))
            tokens = json.loads((private/'access.json').read_text())
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, executable_path=os.getenv('CHROMIUM_PATH') or shutil.which('chromium'), args=['--no-sandbox', '--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream', '--autoplay-policy=no-user-gesture-required', '--auto-select-desktop-capture-source=Entire screen', '--allow-http-screen-capture', '--enable-usermedia-screen-capturing'])
                contexts, errors, failures = [], [], []
                def page_for(identity):
                    context = browser.new_context(viewport={'width':1400, 'height':1000}, permissions=['microphone', 'camera']); contexts.append(context)
                    context.add_init_script('localStorage.setItem("freecollab_token",' + json.dumps(tokens[identity]) + ');localStorage.setItem("freecollab_user_id",' + json.dumps(identity) + ');')
                    context.add_init_script(harness.OBSERVE)
                    page = context.new_page(); page.set_default_timeout(30000); page.on('pageerror', lambda error: errors.append(str(error)))
                    page.on('requestfailed', lambda request: failures.append({'host': urlsplit(request.url).hostname, 'path': urlsplit(request.url).path, 'error': request.failure}))
                    page.goto(app, wait_until='networkidle'); return page
                try:
                    host = page_for('host')
                    # Warm dynamic encrypted-file imports before recording so
                    # Vite's first dependency optimization cannot reload a draft.
                    for attempt in range(5):
                        try:
                            host.evaluate('''async path=>{const {getSecureMessaging}=await import(path);await(await getSecureMessaging()).handles('shared')}''', '/@fs' + str(ROOT/'frontend/src/lib/secure-client.ts'))
                            host.wait_for_timeout(700); host.reload(wait_until='networkidle')
                        except Exception:
                            if attempt == 4: raise
                            host.wait_for_load_state('domcontentloaded'); host.wait_for_timeout(700)
                    for source, label in [('audio', 'Voice note'), ('camera', 'Camera clip'), ('screen', 'Screen walkthrough')]:
                        stage = 'actual ' + source + ' MediaRecorder clip'
                        host.get_by_role('button', name=label, exact=True).click()
                        host.get_by_label('I have permission from everyone whose voice, image or screen content I record.', exact=True).check()
                        host.get_by_role('button', name='Start recording', exact=True).click(); expect(host.get_by_role('button', name='Stop recording', exact=True)).to_be_visible(); host.wait_for_timeout(1800)
                        host.get_by_role('button', name='Stop recording', exact=True).click(); expect(host.get_by_text('Clip title', exact=True)).to_be_visible()
                        draft = host.locator('.fc-clip-draft'); draft.locator('input.fc-input').fill('Private ' + source + ' update')
                        host.get_by_label('Transcript', exact=True).fill('Reviewed ' + source + ' transcript')
                        if source == 'audio' and os.getenv('CLIPS_TEST_LOCAL_ASR'):
                            host.get_by_label('Download the pinned Whisper model to this browser for local transcription. My audio stays on this device.', exact=True).check()
                            host.get_by_role('button', name='Transcribe on this device', exact=True).click()
                            expect(host.get_by_role('button', name='Cancel transcription', exact=True)).to_be_visible()
                            expect(host.get_by_role('button', name='Publish clip', exact=True)).to_be_enabled(timeout=180000)
                            assert not host.get_by_role('alert').count(), 'Actual local inference failed: ' + ' | '.join(host.get_by_role('alert').all_text_contents())
                            assert host.get_by_label('Transcript', exact=True).input_value() != 'Reviewed audio transcript', 'Inference must replace the draft with actual model output'
                            results['actual_local_whisper_onnx_inference'] = True
                            host.get_by_label('Transcript', exact=True).fill('Reviewed audio transcript')
                        host.get_by_role('button', name='Publish clip', exact=True).click(); card = host.locator('.fc-clip-card').filter(has_text='Private ' + source + ' update'); expect(card).to_be_visible()
                        card.get_by_role('button', name='Play recording', exact=True).click(); media = card.locator('audio' if source == 'audio' else 'video')
                        media.evaluate('(element)=>element.play()'); host.wait_for_function('(selector)=>document.querySelector(selector)?.currentTime>0.2', arg='.fc-clip-card ' + ('audio' if source == 'audio' else 'video'))
                        if source != 'audio': assert media.evaluate('element=>element.videoWidth>0')
                        assert host.evaluate('window.__meetingStreams.every(stream=>stream.getTracks().every(track=>track.readyState==="ended"))')
                        results['native_' + source + '_record_upload_download_decode_playback_and_track_cleanup'] = True
                    with sqlite3.connect(private/'proof.db') as db:
                        rows = list(db.execute('SELECT encrypted_payload FROM channelclip')); assert len(rows) == 3 and all('Reviewed' not in row[0] for row in rows)
                    host.reload(wait_until='networkidle'); expect(host.locator('.fc-clip-card')).to_have_count(3); results['clip_metadata_survives_reload_and_transcripts_encrypted_at_rest'] = True
                    if binary:
                        stage = 'actual seven-device LiveKit SFU media'
                        host.get_by_role('button', name='Refresh meetings', exact=True).click(); expect(host.get_by_role('button', name='Start larger meeting', exact=True)).to_be_enabled()
                        host.get_by_role('button', name='Start larger meeting', exact=True).click(); expect(host.get_by_label('Active channel meeting', exact=True)).to_be_visible()
                        peers = []
                        for identity in ['peer', 'p3', 'p4', 'p5', 'p6', 'p7']:
                            page = page_for(identity)
                            if identity != 'peer': page.get_by_label('Start with camera', exact=True).uncheck()
                            page.get_by_role('button', name='Join meeting', exact=True).click()
                            expect(page.get_by_label('Active channel meeting', exact=True)).to_be_visible()
                            page.wait_for_function('window.__meetingPCs.some(pc=>pc.connectionState==="connected")', timeout=45000)
                            peers.append(page)
                        host.wait_for_function('window.__meetingPCs.some(pc=>pc.connectionState==="connected")', timeout=45000)
                        peers[0].wait_for_function('''async()=>{for(const pc of window.__meetingPCs){const stats=await pc.getStats();if([...stats.values()].some(row=>row.type==='inbound-rtp'&&row.kind==='video'&&row.framesDecoded>0&&row.bytesReceived>0))return true}return false}''', timeout=45000)
                        with sqlite3.connect(private/'proof.db') as db: assert db.execute("SELECT count(*) FROM sfuparticipant WHERE state='admitted'").fetchone()[0] == 7
                        results['actual_livekit_seven_devices_scoped_tokens_real_inbound_video_rtp'] = True
                        stage = 'closing tools preserves active media and host leave preserves SFU room'
                        host.evaluate('window.proofSetTools(false)'); expect(host.get_by_label('Active channel meeting', exact=True)).to_be_visible(); assert host.evaluate('window.__meetingStreams.some(stream=>stream.getAudioTracks().some(track=>track.readyState==="live"))')
                        host.get_by_role('button', name='Leave meeting', exact=True).click(); expect(peers[0].get_by_label('Active channel meeting', exact=True)).to_be_visible()
                        with sqlite3.connect(private/'proof.db') as db: assert db.execute('SELECT ended_at FROM sfuroom').fetchone()[0] is None
                        results['dock_persists_while_tools_closed_and_sfu_room_survives_host_leave'] = True
                        stage = 'worker revocation removes a live SFU device without browser polling'
                        victim = peers[-1]; victim.route('**/large-meetings/*/participants/*/heartbeat', lambda route: route.fulfill(status=200, content_type='application/json', body='{"active":true}'))
                        with sqlite3.connect(private/'proof.db') as db: db.execute("DELETE FROM channelmember WHERE channel_id='shared' AND user_id='p7'"); db.commit()
                        expect(victim.get_by_role('alert')).to_be_visible(timeout=45000); harness.assert_stopped(victim)
                        results['independent_worker_source_membership_revocation_disconnects_sfu_and_stops_media'] = True
                    assert not errors, errors; results['zero_browser_page_errors'] = True
                finally:
                    for context in contexts: context.close()
                    browser.close()
        except Exception:
            print('FAILED media browser stage: ' + stage, file=sys.stderr)
            if 'failures' in locals(): print('Request failures: ' + json.dumps(failures[-8:]), file=sys.stderr)
            for name in ['livekit.log', 'api.log', 'worker.log', 'vite.log']:
                path = private/name
                if path.exists():
                    # Logs contain no request/header/token bodies; truncate framework diagnostics.
                    print(name + ': ' + path.read_text()[-3000:], file=sys.stderr)
            raise
        finally:
            for process in reversed(processes):
                if process.poll() is None: process.terminate()
            for process in reversed(processes):
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
            for stream in streams: stream.close()
    REPORT.parent.mkdir(parents=True, exist_ok=True); REPORT.write_text(json.dumps({'results': results, 'scope': 'Actual synthetic-device Chromium capture/encoding/decoding/playback; actual LiveKit only when binary explicitly supplied; no media API mocks', 'local_asr_model': 'Official pinned model bytes from explicit same-origin operator mirror' if os.getenv('CLIPS_TEST_MODEL_DIRECTORY') else 'Default model origin; actual inference only when ASR enabled', 'livekit_binary_supplied': bool(binary), 'cleanup': 'Own isolated services, browser devices, private database, generated credentials and files removed; caller-owned model mirror/binary retained'}, indent=2) + '\n')
    print('Media browser proof passed: ' + str(len(results)) + ' checks.')


if __name__ == '__main__': main()
