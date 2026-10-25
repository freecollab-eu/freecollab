"""Actual verified Matrix signaling, WebRTC media and consented device recording.

Called only by the disposable two-Synapse fixture; fake browser devices provide
real media tracks. This does not claim physical microphone or ASR acceptance.
"""
import json
import time


def run_media_stage(pages, checks):
    print('Federation proof stage: real verified Matrix WebRTC media and consented recording',flush=True)
    for page in pages:
        page.evaluate("async()=>{const {api}=await import('/src/lib/api.ts');localStorage.setItem('freecollab_user_id',api.getCurrentUserId());localStorage.setItem('freecollab_token',api.getAccessToken());}")
    host=pages[0].evaluate("""async()=>{const m=await import('/src/lib/federated-meeting.ts'),a=await import('/src/lib/active-meeting.ts'),c=await import('/src/lib/matrix-meeting-capture.ts');window.mediaRoom=await m.MatrixMeetingRoom.open(window.proofChannel);window.mediaCapture=new c.MatrixCaptureAuthority(window.mediaRoom);const tracks=await navigator.mediaDevices.getUserMedia({audio:true,video:true});a.startMatrixMeeting(window.mediaRoom,tracks,undefined,window.mediaCapture);return window.mediaRoom.meeting.id}""")
    pages[1].evaluate("""async id=>{const m=await import('/src/lib/federated-meeting.ts'),a=await import('/src/lib/active-meeting.ts'),c=await import('/src/lib/matrix-meeting-capture.ts');window.mediaRoom=await m.MatrixMeetingRoom.open(window.proofChannel,id);window.mediaCapture=new c.MatrixCaptureAuthority(window.mediaRoom);const tracks=await navigator.mediaDevices.getUserMedia({audio:true,video:true});a.startMatrixMeeting(window.mediaRoom,tracks,undefined,window.mediaCapture)}""",host)
    for page in pages:
        page.evaluate("""async()=>{const {React,createRoot}=window.fixtureModules,{MeetingDock}=await import('/src/components/MeetingDock.tsx');document.getElementById('work-proof').style.display='none';const node=document.createElement('div');node.id='media-proof';document.body.append(node);window.mediaUI=createRoot(node);window.mediaUI.render(React.createElement(MeetingDock))}""")
    host_ui=pages[0].locator('#media-proof');peer_ui=pages[1].locator('#media-proof')
    host_ui.get_by_role('button',name='Admit',exact=True).wait_for(timeout=90000)
    host_ui.get_by_role('button',name='Admit',exact=True).click()
    for page in pages:
        deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            connected=page.evaluate("async()=>{const a=await import('/src/lib/active-meeting.ts');return a.currentMeeting()?.view?.connected||0}")
            if connected==1:break
            page.wait_for_timeout(300)
        else:raise AssertionError('Actual WebRTC peer connection did not connect across verified Matrix signaling')
        packets=page.evaluate("""async()=>{const a=await import('/src/lib/active-meeting.ts'),call=a.currentMeeting();let sent=0;for(const peer of call.connection.peers.values()){for(const row of(await peer.pc.getStats()).values())if(row.type==='outbound-rtp'&&row.kind==='audio')sent+=row.packetsSent||0;}return sent}""")
        assert packets>0,'The actual browser audio RTP sender had no packets'
    checks['actual_verified_Matrix_encrypted_SDP_ICE_and_bidirectional_browser_WebRTC_RTP']=True
    host_ui.get_by_text('Set up automatic recording',exact=True).click()
    host_ui.get_by_role('button',name='Ask everyone and record automatically',exact=True).click()
    peer_ui.get_by_role('button',name='I agree to the disclosed recording and processing',exact=True).wait_for(timeout=90000)
    # Before that second device's explicit consent, the recorder must not exist.
    assert host_ui.locator('.fc-recording-dot').count()==0
    peer_ui.get_by_role('button',name='I agree to the disclosed recording and processing',exact=True).click()
    host_ui.locator('.fc-recording-dot').wait_for(timeout=90000)
    pages[0].wait_for_timeout(1500)
    host_ui.get_by_role('button',name='Pause recording',exact=True).click()
    host_ui.get_by_role('button',name='Resume recording',exact=True).wait_for(timeout=60000)
    host_ui.get_by_role('button',name='Resume recording',exact=True).click()
    # Hold only delivery of the genuine final stop response, after all its real
    # encrypted authority writes. This exceeds the UI's six-second local bound.
    pages[0].evaluate("""()=>{const original=window.mediaCapture.control.bind(window.mediaCapture);window.mediaCapture.control=async(...args)=>{const result=await original(...args);if(args[1]==='stop'&&args[2])await new Promise(resolve=>setTimeout(resolve,9000));return result}}""")
    host_ui.get_by_role('button',name='Stop and save recording',exact=True).click()
    deadline=time.monotonic()+120;row=None
    while time.monotonic()<deadline:
        row=pages[1].evaluate("""async()=>{const c=await import('/src/lib/clips-api.ts');const rows=await c.clipsApi.list(window.proofChannel);const row=rows.find(r=>r.fields?.title.startsWith('Meeting recording'));if(!row)return null;const blob=await c.clipsApi.download(row);window.recordedPeerClip=row;window.recordedPeerBlob=blob;return {id:row.id,size:blob.size,type:blob.type,encrypted:row.file.encrypted,proof:row.file.id}}""")
        if row:break
        pages[1].wait_for_timeout(500)
    if not row:
        diagnostics=[]
        for page in pages:
            diagnostics.append(page.evaluate("""async()=>{const output={controls:document.getElementById('media-proof')?.innerText.slice(-12000)};try{output.capture=await window.mediaCapture.status()}catch(error){output.captureError=String(error)}try{const vault=await import('/src/lib/meeting-recording-vault.ts');output.pending=(await vault.pendingMeetingRecordings(window.proofChannel)).map(({data,publication_proof,publication_receipt,...row})=>({...row,bytes:data.length,hasReceipt:!!publication_receipt,proof:publication_proof?{protocol:publication_proof.protocol,capture_id:publication_proof.capture_id,clip_id:publication_proof.clip_id}:null}))}catch(error){output.vaultError=String(error)}try{const {clipsApi}=await import('/src/lib/clips-api.ts');output.clips=(await clipsApi.list(window.proofChannel)).map(row=>({id:row.id,title:row.fields?.title,file:row.file?.id,error:row.resolution_error}))}catch(error){output.clipsError=String(error)}try{const s=await(await import('/src/lib/secure-client.ts')).getSecureMessaging();output.artifactEvents=(await s.events(window.proofChannel)).filter(event=>['eu.freecollab.clip','eu.freecollab.file'].includes(event.getType())).map(event=>({id:event.getId(),type:event.getType(),hasProof:!!event.getContent().meeting_capture,request:event.getContent().request_id}))}catch(error){output.eventsError=String(error)}return output}"""))
        print('Actual capture publication diagnostics: '+json.dumps(diagnostics),flush=True)
    assert row and row['size']>100 and row['encrypted'] is True and row['id'].startswith('$'),'Consented MediaRecorder output was not durably saved and decrypted on the peer installation'
    playable=pages[1].evaluate("""async()=>{const audio=document.createElement('audio');audio.src=URL.createObjectURL(window.recordedPeerBlob);audio.preload='metadata';return await new Promise((resolve,reject)=>{audio.onloadedmetadata=()=>resolve(audio.duration>0);audio.onerror=()=>reject(Error('The actual recorded WebM could not decode'));document.body.append(audio);audio.load();})}""")
    assert playable,'The peer could not decode the actual captured audio'
    host_ui.get_by_text('Recording saved to channel clips.',exact=True).wait_for(timeout=60000)
    checks['actual_delayed_original_stop_receipt_reconciles_after_six_second_bound_without_manual_retry']=True
    checks['actual_React_all_device_consent_pause_resume_stop_MediaRecorder_private_vault_and_peer_clip_decrypt']=True
    # A forged disclosure cannot reuse a host's consent as the peer's signature.
    denied=pages[1].evaluate("""async()=>{const {getSecureMessaging}=await import('/src/lib/secure-client.ts'),{verifyMatrixRecordingProof}=await import('/src/lib/matrix-meeting-capture.ts'),s=await getSecureMessaging(),event=await s.getDecryptedEvent(window.proofChannel,window.recordedPeerClip.file.id),proof=structuredClone(event.getContent().meeting_capture);proof.participants[1].consent_event_id=proof.participants[0].consent_event_id;try{await verifyMatrixRecordingProof(window.proofChannel,proof);return false}catch{return true}}""")
    assert denied
    checks['actual_encrypted_recording_proof_rejects_reused_or_forged_participant_consent']=True
    terminal_proof=pages[1].evaluate("""async()=>{const s=await(await import('/src/lib/secure-client.ts')).getSecureMessaging();return(await s.getDecryptedEvent(window.proofChannel,window.recordedPeerClip.file.id)).getContent().meeting_capture}""")
    forged_terminal=pages[0].evaluate("""async proof=>{await window.mediaRoom.publishCapture('capture-state',{capture_id:proof.capture_id,generation:proof.generation,request_event_id:proof.request_event_id,state:'recording'});const clip=crypto.randomUUID(),publication=await window.mediaRoom.publishCapture('capture-publish',{capture_id:proof.capture_id,generation:proof.generation,request_event_id:proof.request_event_id,clip_id:clip,consent_event_ids:proof.participants.map(row=>row.consent_event_id)});return {...proof,publication_event_id:publication,clip_id:clip}}""",terminal_proof)
    assert pages[1].evaluate("async()=> (await window.mediaCapture.status()).capture.state==='stopped'"),'A direct verified host event restarted a terminal recording'
    assert pages[1].evaluate("""async({original,forged})=>{const {verifyMatrixRecordingProof}=await import('/src/lib/matrix-meeting-capture.ts');await verifyMatrixRecordingProof(window.proofChannel,original);try{await verifyMatrixRecordingProof(window.proofChannel,forged);return false}catch(error){return String(error).includes('stopped recording')}}""",{'original':terminal_proof,'forged':forged_terminal}),'Old consent authorized a new direct publication after stop'
    checks['actual_verified_host_direct_restart_and_new_post_stop_publication_fail_original_receipt_stays_valid']=True
    run_caption_release_stage(pages,checks)
    omitted=pages[0].evaluate("""async()=>{const {clipsApi}=await import('/src/lib/clips-api.ts'),{getSecureMessaging}=await import('/src/lib/secure-client.ts'),{verifyMatrixRecordingProof}=await import('/src/lib/matrix-meeting-capture.ts'),s=await getSecureMessaging(),row=(await clipsApi.list(window.proofChannel)).find(row=>row.fields?.title.startsWith('Meeting recording')),event=await s.getDecryptedEvent(window.proofChannel,row.file.id),original=event.getContent().meeting_capture,own=original.participants.find(row=>row.matrix_user_id===window.mediaRoom.matrixUser),capture=crypto.randomUUID(),roster=[{participant_id:own.participant_id,matrix_user_id:own.matrix_user_id,device_id:own.device_id,join_event_id:own.join_event_id}],request=await window.mediaRoom.publishCapture('capture-request',{capture_id:capture,generation:1,roster,video:false,captions:false,save_transcript:false,language:'auto',caption_audience:'channel'}),consent=await window.mediaRoom.publishCapture('capture-consent',{capture_id:capture,generation:1,request_event_id:request,granted:true}),clip=crypto.randomUUID(),publication=await window.mediaRoom.publishCapture('capture-publish',{capture_id:capture,generation:1,request_event_id:request,clip_id:clip,consent_event_ids:[consent]}),forged={...original,capture_id:capture,request_event_id:request,publication_event_id:publication,clip_id:clip,participants:[{...own,consent_event_id:consent}]};try{await verifyMatrixRecordingProof(window.proofChannel,forged);return false}catch(error){return String(error).includes('omitted')}}""")
    assert omitted,'A real verified host omitted an actually admitted peer from its recording disclosure'
    checks['actual_indexed_admission_frontier_rejects_verified_host_omitting_admitted_peer']=True
    for page in pages:
        page.evaluate("async()=>{const a=await import('/src/lib/active-meeting.ts');await a.leaveActiveMeeting();window.mediaUI.unmount();document.getElementById('media-proof').remove();document.getElementById('work-proof').style.display='';}")


def run_caption_release_stage(pages,checks):
    """Transport/privacy acceptance: actual Rust encryption, no ASR claim."""
    print('Federation proof stage: actual caption audience and final encrypted HTTP release fence',flush=True)
    sent=[]
    def observe(request):
        if request.method=='PUT'and'/send/m.room.encrypted/fc-capture-caption-'in request.url:sent.append(request.url)
    for page in pages:
        page.on('request',observe)
        page.evaluate('()=>window.mediaUI.unmount()')
    try:
        denied=pages[0].evaluate("""async()=>{for(const audience of [undefined,'admitted']){try{await window.mediaCapture.request({enabled:true,video:false,captions:true,save_transcript:false,language:'en',...(audience?{caption_audience:audience}:{})});return false}catch(error){if(!String(error).includes('all encrypted channel members'))throw error}}return true}""")
        assert denied,'An unreviewed caption recipient audience was accepted'
        capture=pages[0].evaluate("""async()=>{const result=await window.mediaCapture.request({enabled:true,video:false,captions:true,save_transcript:false,language:'en',caption_audience:'channel'});window.captionPolicy=result.capture;return result.capture}""")
        pages[1].evaluate("async capture=>{await window.mediaCapture.consent(capture,true)}",capture)
        pages[0].evaluate("""async()=>{const current=(await window.mediaCapture.status()).capture;if(!current.all_consented)throw Error('Actual peer consent missing');const result=await window.mediaCapture.control(current,'start');window.captionPolicy=result.capture;await window.mediaCapture.caption(result.capture,{start:0,end:1,text:'Consented channel caption transport fixture'},new AbortController().signal)}""")
        deadline=time.monotonic()+45
        while time.monotonic()<deadline:
            visible=pages[1].evaluate("async()=>{const s=await window.mediaCapture.status();return s.capture?.caption_audience==='channel'&&s.capture.live_captions.some(row=>row.text==='Consented channel caption transport fixture')}")
            if visible:break
            pages[1].wait_for_timeout(300)
        assert visible,'The actual peer did not decrypt the approved channel-audience caption'
        assert len(sent)==1,'The positive caption did not traverse actual encrypted HTTP'
        checks['actual_caption_explicit_channel_audience_both_device_consent_and_peer_decrypt']=True
        pages[0].evaluate("""async()=>{const s=await(await import('/src/lib/secure-client.ts')).getSecureMessaging(),{c}=await s.roomFor(window.proofChannel),crypt=c.getCrypto(),original=crypt.encryptEvent;window.captionCrypto=crypt;window.captionOriginalEncrypt=original;window.captionBarrierReached=false;window.captionReleaseResult=null;crypt.encryptEvent=async function(event,room){if(event.getContent()?.kind==='capture-caption'&&event.getContent()?.text==='Blocked after actual peer withdrawal'){window.captionHeldEvent=event;window.captionBarrierReached=true;await new Promise(resolve=>window.releaseCaptionEncryption=resolve);}return original.call(this,event,room)};window.captionReleasePending=window.mediaCapture.caption(window.captionPolicy,{start:1,end:2,text:'Blocked after actual peer withdrawal'},new AbortController().signal).then(()=>{window.captionReleaseResult={accepted:true}},error=>{window.captionReleaseResult={accepted:false,error:String(error)}})}""")
        pages[0].wait_for_function('window.captionBarrierReached||window.captionReleaseResult',timeout=60000)
        assert pages[0].evaluate('window.captionBarrierReached'),'Caption did not reach the actual Rust encryption boundary: '+str(pages[0].evaluate('window.captionReleaseResult'))
        pages[1].evaluate("async capture=>{await window.mediaCapture.consent(capture,false)}",capture)
        assert pages[0].evaluate("async()=>!(await window.mediaCapture.status()).capture.all_consented"),'The host did not observe committed withdrawal'
        pages[0].evaluate('()=>window.releaseCaptionEncryption()')
        pages[0].wait_for_function('window.captionReleaseResult',timeout=60000)
        result=pages[0].evaluate('window.captionReleaseResult');assert not result['accepted'],result
        assert len(sent)==1,'A caption escaped through encrypted HTTP after observed withdrawal'
        pages[0].evaluate('()=>{window.captionCrypto.encryptEvent=window.captionOriginalEncrypt}')
        rejected=pages[0].evaluate("""async()=>{const s=await(await import('/src/lib/secure-client.ts')).getSecureMessaging(),{c,room,roomId}=await s.roomFor(window.proofChannel);let retry=false,raw=false;try{await c.resendEvent(window.captionHeldEvent,room)}catch{retry=true}try{await c.sendEvent(roomId,'eu.freecollab.meeting',{protocol:'freecollab-matrix-meeting/1',kind:'capture-caption',text:'No live caption grant after reload'},'fc-capture-caption-reload-'+crypto.randomUUID())}catch{raw=true}return {retry,raw}}""")
        assert rejected=={'retry':True,'raw':True},rejected
        assert len(sent)==1,'An SDK retry without its live caption authorization reached encrypted HTTP'
        unseen=pages[1].evaluate("""async()=>{const s=await(await import('/src/lib/secure-client.ts')).getSecureMessaging();return !(await s.events(window.proofChannel)).some(event=>event.getContent()?.text==='Blocked after actual peer withdrawal'||event.getContent()?.text==='No live caption grant after reload')}""")
        assert unseen,'The peer received a caption after withdrawal/reload'
        checks['actual_Rust_caption_encryption_wait_peer_withdraw_and_final_HTTP_guard_denial']=True
        checks['actual_caption_SDK_retry_and_raw_reload_without_live_guard_fail_closed']=True
        pages[0].evaluate("async()=>{const capture=(await window.mediaCapture.status()).capture;await window.mediaCapture.control(capture,'stop')}")
    finally:
        for page in pages:
            page.remove_listener('request',observe)
        try:pages[0].evaluate('()=>{if(window.captionCrypto&&window.captionOriginalEncrypt)window.captionCrypto.encryptEvent=window.captionOriginalEncrypt;if(window.releaseCaptionEncryption)window.releaseCaptionEncryption();}')
        except Exception:pass
