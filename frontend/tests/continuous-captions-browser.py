#!/usr/bin/env python3
"""Actual continuous Whisper worker inference over locally generated speech.

Uses libflite for known synthetic speech, Chromium's real AudioContext/MediaStream,
and the product LocalMeetingCaptions/Worker/WASM implementation. The official
model mirror must be prepared with scripts/prepare-whisper-model.py. No audio is
sent to any server; the only browser requests are same-origin module/model GETs.
"""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import urlopen
import wave

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
REVISION = 'ff4177021cc41f7db950912b73ea4fdf7d01d8e7'
SPEECH = 'The meeting starts at nine. We will review the design and share the notes. The meeting starts at nine. We will review the design and share the notes.'
SOURCES = ['frontend/src/lib/meeting-captions.ts', 'frontend/src/lib/meeting-captions.worker.ts',
           'frontend/package-lock.json', 'scripts/prepare-whisper-model.py',
           'frontend/tests/continuous-captions-browser.py', 'frontend/tests/meeting-caption-drain.test.mjs']

ENTRY = r'''import {LocalMeetingCaptions} from __SOURCE__;
const state={outputs:[],errors:[],progress:[],starts:[],stops:[]};window.proof=state;
let engine, audio, source, destination, speechBuffer;
window.begin=async()=>{
 const bytes=await(await fetch('/known-speech.wav')).arrayBuffer();
 audio=new AudioContext({sampleRate:48000});await audio.resume();
 speechBuffer=await audio.decodeAudioData(bytes);source=audio.createBufferSource();source.buffer=speechBuffer;source.loop=true;
 destination=audio.createMediaStreamDestination();source.connect(destination);
 engine=new LocalMeetingCaptions({captions:true,model:'onnx-community/whisper-tiny',model_base:'/whisper-models/'},'en',
   caption=>state.outputs.push({...caption,at:performance.now()}),message=>state.errors.push(message),message=>state.progress.push(message));
 await engine.prepare();source.start();await engine.start(destination.stream);
 state.starts.push({at:performance.now(),input_rate:audio.sampleRate,caption_rate:engine.context?.sampleRate,input_track:destination.stream.getAudioTracks()[0]?.readyState});
 return state.starts.at(-1);
};
window.pause=value=>engine.pause(value);
window.restartKnownSpeech=()=>{source.stop();source=audio.createBufferSource();source.buffer=speechBuffer;source.loop=true;source.connect(destination);source.start()};
window.captionState=()=>({active_seconds:engine.activeSeconds,output_count:state.outputs.length,in_flight:!!engine.inFlight});
window.finish=async(timeout=6000,through)=>{
 const start=performance.now();try{await engine.drain(timeout,through);return{ok:true,elapsed:performance.now()-start,...window.captionState()}}
 catch(error){return{ok:false,name:error.name,message:error.message,elapsed:performance.now()-start,...window.captionState()}}
};
window.rejectForeignMirror=async()=>{
 const bad=new LocalMeetingCaptions({captions:true,model:'onnx-community/whisper-tiny',model_base:'//untrusted/'},'en',()=>{},()=>{});
 try{await bad.prepare();return{rejected:false}}catch(error){return{rejected:true,message:error.message}}finally{bad.stop()}
};
window.stop=async()=>{
 const ownContext=engine?.context;engine?.stop();
 await new Promise(resolve=>setTimeout(resolve,150));
 state.stops.push({worker_released:engine?.worker===null,caption_context_released:engine?.context===null,
   context_closed:!ownContext||ownContext.state==='closed',shared_input_still_live:destination?.stream.getAudioTracks()[0]?.readyState==='live'});
 source?.stop();destination?.stream.getTracks().forEach(track=>track.stop());await audio?.close();return state.stops.at(-1);
};
document.body.innerHTML='<main><h1>Continuous captions: actual device pipeline</h1><p>Known locally generated speech. Real pinned Whisper. No audio upload.</p></main>';
'''


def port():
    with socket.socket() as server:
        server.bind(('127.0.0.1', 0)); return server.getsockname()[1]


def speech(path):
    base = ctypes.CDLL('libflite.so.1', mode=ctypes.RTLD_GLOBAL)
    voice = ctypes.CDLL('libflite_cmu_us_slt.so.1', mode=ctypes.RTLD_GLOBAL)
    base.flite_init()
    voice.register_cmu_us_slt.argtypes = [ctypes.c_char_p]; voice.register_cmu_us_slt.restype = ctypes.c_void_p
    base.flite_text_to_wave.argtypes = [ctypes.c_char_p, ctypes.c_void_p]; base.flite_text_to_wave.restype = ctypes.c_void_p
    base.cst_wave_save_riff.argtypes = [ctypes.c_void_p, ctypes.c_char_p]; base.cst_wave_save_riff.restype = ctypes.c_int
    base.delete_wave.argtypes = [ctypes.c_void_p]
    value = base.flite_text_to_wave(SPEECH.encode(), voice.register_cmu_us_slt(None))
    assert value and base.cst_wave_save_riff(value, str(path).encode()) == 0
    base.delete_wave(value)
    with wave.open(str(path)) as audio:
        return {'rate': audio.getframerate(), 'seconds': audio.getnframes() / audio.getframerate(),
                'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'transcript': SPEECH}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-directory', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    mirror = args.model_directory.resolve()
    manifest = json.loads((mirror / 'onnx-community/whisper-tiny/resolve' / REVISION / 'freecollab-model-manifest.json').read_text())
    assert manifest['revision'] == REVISION
    for record in manifest['files']:
        path = mirror / 'onnx-community/whisper-tiny/resolve' / REVISION / record['path']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == record['sha256']
    hashes = lambda: {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}
    before = hashes(); result = {'status': 'running', 'checks': {}, 'before': before, 'model': manifest,
        'scope': 'Known libflite speech, actual Chromium audio windows and pinned Whisper WASM; adversarial stalled worker is paused via Chromium debugger, not replaced.'}
    processes = []; streams = []; stage = 'startup'; errors = []; requests = []; failed = []; console = []
    args.report.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix='freecollab-continuous-asr-') as directory:
            private = Path(directory); web = private/'web'; web.mkdir(); (web/'public').mkdir()
            (web/'public/whisper-models').symlink_to(mirror, target_is_directory=True)
            result['speech'] = speech(web/'public/known-speech.wav')
            (web/'index.html').write_text('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><script type="module" src="/entry.ts"></script></body></html>')
            (web/'entry.ts').write_text(ENTRY.replace('__SOURCE__', json.dumps(str(ROOT/'frontend/src/lib/meeting-captions.ts'))))
            number = port(); app = f'http://127.0.0.1:{number}'
            launcher = private/'vite.mjs'
            launcher.write_text('import{createServer}from '+json.dumps(str(ROOT/'frontend/node_modules/vite/dist/node/index.js'))+';const server=await createServer({configFile:false,root:'+json.dumps(str(web))+',cacheDir:'+json.dumps(str(private/'cache'))+',server:{host:"127.0.0.1",port:'+str(number)+',strictPort:true,hmr:false,fs:{allow:'+json.dumps([str(web),str(ROOT/'frontend'),str(mirror)])+'}},optimizeDeps:{include:["@huggingface/transformers"]}});await server.listen();')
            (web/'node_modules').symlink_to(ROOT/'frontend/node_modules', target_is_directory=True)
            log = (private/'vite.log').open('w'); streams.append(log)
            processes.append(subprocess.Popen(['node',str(launcher)],cwd=ROOT/'frontend',stdout=log,stderr=log,start_new_session=True))
            deadline = time.monotonic()+45
            while True:
                assert processes[0].poll() is None, (private/'vite.log').read_text()[-2000:]
                try:
                    with urlopen(app,timeout=1) as response:
                        if response.status == 200: break
                except OSError: pass
                assert time.monotonic()<deadline; time.sleep(.1)
            with sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH') or '/usr/bin/chromium',headless=True,
                    args=['--no-sandbox','--autoplay-policy=no-user-gesture-required'])
                page = browser.new_page(); page.set_default_timeout(180000)
                page.on('pageerror',lambda error: errors.append(str(error)))
                page.on('console',lambda message: console.append({'type':message.type,'text':message.text[:500]}))
                page.on('request',lambda request: requests.append({'method':request.method,'url':request.url}))
                page.on('requestfailed',lambda request: failed.append({'url':request.url,'error':request.failure}))
                page.goto(app,wait_until='networkidle'); page.wait_for_function('()=>typeof window.begin==="function"')
                stage = 'real local model load and 48kHz MediaStream input'
                started = page.evaluate('()=>window.begin()'); assert started['input_rate']==48000 and started['input_track']=='live'
                result['checks']['actual_48khz_audio_context_and_media_stream_started'] = True
                stage = 'real first continuous Whisper inference'
                page.wait_for_function('()=>window.proof.outputs.length>=1||window.proof.errors.length>0',timeout=90000)
                state = page.evaluate('window.proof'); assert not state['errors'],state['errors']
                assert any(word in state['outputs'][0]['text'].lower() for word in ['meeting','review','design','notes']),state['outputs']
                assert state['outputs'][0]['start']==0 and state['outputs'][0]['end']==5
                result['checks']['actual_worker_wasm_recognizes_known_speech_in_first_resampled_window'] = True
                stage = 'pause excludes captions and resumes real inference'
                page.evaluate('()=>window.pause(true)'); count = page.evaluate('window.proof.outputs.length'); page.wait_for_timeout(2200)
                assert page.evaluate('window.proof.outputs.length')==count
                page.evaluate('()=>{window.restartKnownSpeech();window.pause(false)}')
                page.wait_for_function('(count)=>window.proof.outputs.length>count||window.proof.errors.length>0',arg=count,timeout=90000)
                state = page.evaluate('window.proof'); assert not state['errors'],state['errors']
                assert state['outputs'][-1]['start']>=5 and all(len(row['text'])<=2000 for row in state['outputs'])
                result['checks']['pause_omits_audio_and_resume_produces_subsequent_real_window'] = True
                stage = 'stop cancels actual worker and closes owned audio graph'
                stopped = page.evaluate('()=>window.stop()'); assert all(stopped.values()),stopped
                count = page.evaluate('window.proof.outputs.length'); page.wait_for_timeout(1200)
                assert page.evaluate('window.proof.outputs.length')==count
                result['positive_run'] = page.evaluate('window.proof')
                result['checks']['stop_terminates_worker_closes_own_context_and_preserves_shared_track_ownership'] = True
                stage = 'adversarial real-worker stall fails within bounded backlog'
                page.evaluate('()=>window.begin()')
                cdp = browser.new_browser_cdp_session(); messages=[]
                cdp.on('Target.receivedMessageFromTarget',lambda message: messages.append(json.loads(message['message'])))
                targets=cdp.send('Target.getTargets')['targetInfos']
                target=next(row for row in targets if row['type']=='worker' and 'meeting-captions.worker' in row['url'])
                session=cdp.send('Target.attachToTarget',{'targetId':target['targetId'],'flatten':False})['sessionId']
                for number,method in [(1,'Debugger.enable'),(2,'Debugger.pause')]:
                    cdp.send('Target.sendMessageToTarget',{'sessionId':session,'message':json.dumps({'id':number,'method':method})})
                deadline=time.monotonic()+8
                while not any(message.get('method')=='Debugger.paused' for message in messages):
                    assert time.monotonic()<deadline,'Actual dedicated worker did not pause';page.wait_for_timeout(50)
                failure_count=page.evaluate('window.proof.errors.length')
                page.wait_for_function('(count)=>window.proof.errors.length>count',arg=failure_count,timeout=25000)
                state=page.evaluate('window.proof')
                assert 'cannot keep up' in state['errors'][-1] and 'fallback' in state['errors'][-1],state['errors']
                result['checks']['actual_stalled_worker_hits_bounded_backpressure_and_never_uses_network_fallback'] = True
                page.evaluate('()=>window.stop()'); result['final_run']=page.evaluate('window.proof')
                stage = 'actual partial-window completion and pause clock alignment'
                page.evaluate('()=>window.begin()')
                page.wait_for_function('()=>window.captionState().active_seconds>=3.2',timeout=12000)
                page.evaluate('()=>window.pause(true)'); paused=page.evaluate('window.captionState()'); before_count=paused['output_count']
                assert paused['active_seconds']<4.5,paused
                first=page.evaluate('()=>window.finish(12000,1.25)');assert first['ok'],first
                values=page.evaluate('window.proof.outputs');assert values[-1]['start']==0 and values[-1]['end']==1.25,values[-1]
                assert values[-1]['text'].strip(),values[-1]
                completed=page.evaluate('()=>window.finish(12000)');assert completed['ok'],completed
                values=page.evaluate('window.proof.outputs');assert abs(values[-1]['end']-paused['active_seconds'])<.0001,values[-1]
                assert values[-1]['end']-values[-1]['start']<5
                page.wait_for_timeout(1200)
                assert page.evaluate('window.captionState().active_seconds')==paused['active_seconds']
                result['checks']['actual_partial_drain_honors_segment_frontier_and_preserves_pre_pause_speech']=True
                page.evaluate('()=>{window.restartKnownSpeech();window.pause(false)}')
                page.wait_for_function('(start)=>window.captionState().active_seconds>=start+2.2',arg=paused['active_seconds'],timeout=10000)
                page.evaluate('()=>window.pause(true)'); end=page.evaluate('window.captionState().active_seconds')
                final=page.evaluate('()=>window.finish(12000)');assert final['ok'],final
                values=page.evaluate('window.proof.outputs');last=values[-1]
                assert abs(last['start']-paused['active_seconds'])<.0001 and abs(last['end']-end)<.0001,last
                assert 2<=last['end']-last['start']<4 and any(word in last['text'].lower() for word in ['meeting','review','design','nine','9']),last
                assert page.evaluate('window.proof.outputs.length')>=before_count+3
                result['checks']['pause_excludes_elapsed_time_and_last_sub_five_second_speech_is_actually_recognized']=True
                result['partial_run']={'paused_active_seconds':paused['active_seconds'],'final_active_seconds':end,'outputs':values[before_count:]}
                page.evaluate('()=>window.stop()')
                stage = 'actual completion timeout and revocation abort against paused dedicated worker'
                page.evaluate('()=>window.begin()')
                page.wait_for_function('()=>window.captionState().active_seconds>=2',timeout=10000)
                page.evaluate('()=>window.pause(true)')
                targets=cdp.send('Target.getTargets')['targetInfos'];target=next(row for row in targets if row['type']=='worker' and 'meeting-captions.worker' in row['url'])
                session=cdp.send('Target.attachToTarget',{'targetId':target['targetId'],'flatten':False})['sessionId'];messages.clear()
                for number,method in [(10,'Debugger.enable'),(11,'Debugger.pause')]:
                    cdp.send('Target.sendMessageToTarget',{'sessionId':session,'message':json.dumps({'id':number,'method':method})})
                page.evaluate('()=>{window.pendingTimeout=window.finish(350);return true}')
                deadline=time.monotonic()+5
                while not any(message.get('method')=='Debugger.paused' for message in messages):
                    assert time.monotonic()<deadline;page.wait_for_timeout(30)
                count=page.evaluate('window.proof.outputs.length')
                timed=page.evaluate('()=>window.pendingTimeout');assert not timed['ok'] and timed['name']=='TimeoutError' and timed['elapsed']<3000,timed
                page.evaluate('()=>{window.pendingFinish=window.finish(5000);return true}')
                page.evaluate('()=>window.stop()');aborted=page.evaluate('()=>window.pendingFinish')
                assert not aborted['ok'] and aborted['name']=='AbortError',aborted
                page.wait_for_timeout(500);assert page.evaluate('window.proof.outputs.length')==count
                result['checks']['actual_worker_stall_has_bounded_drain_timeout_and_revocation_aborts_without_late_caption']=True
                result['completion_failure']={'timeout':timed,'aborted':aborted}
                stage='actual worker rejects a protocol-relative foreign model mirror before any request'
                invalid=page.evaluate('()=>window.rejectForeignMirror()');assert invalid['rejected'],invalid
                result['checks']['protocol_relative_mirror_is_rejected_without_foreign_weight_or_executable_request']=True
                assert not errors,errors
                assert all(row['method']=='GET' and (urlsplit(row['url']).hostname in {'127.0.0.1','localhost'} or urlsplit(row['url']).scheme in {'blob','data'}) for row in requests),requests
                result['checks']['zero_browser_errors_and_no_audio_text_upload_or_external_browser_request'] = True
                result['model_requests']=[row for row in requests if '/whisper-models/' in row['url']]
                result['wasm_requests']=[row for row in requests if '.wasm' in row['url']]
                assert any('encoder_model_quantized.onnx' in row['url'] for row in result['model_requests'])
                assert any('decoder_model_merged_quantized.onnx' in row['url'] for row in result['model_requests'])
                assert result['wasm_requests'],'Actual inference must load the real release WASM'
                result['checks']['prepared_pinned_model_mirror_and_release_wasm_are_actually_fetched'] = True
                browser.close()
            result['vite_log_tail']=(private/'vite.log').read_text()[-2000:]
        result['after']=hashes(); result['sources_unchanged']=before==result['after']
        assert result['sources_unchanged'],'Runtime/test sources changed during proof'
        result['status']='passed'
    finally:
        for process in reversed(processes):
            if process.poll() is None: os.killpg(process.pid,signal.SIGTERM)
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=5)
        for stream in streams:stream.close()
        result.update(stage=stage,browser_errors=errors,request_failures=failed,console=console[-20:],request_count=len(requests),
                      cleanup='Owned Vite/browser/audio fixture removed; caller-owned pinned model mirror retained.')
        args.report.write_text(json.dumps(result,indent=2)+'\n')
    print('Continuous caption browser proof passed: '+str(len(result['checks']))+' checks.')


if __name__=='__main__':main()
