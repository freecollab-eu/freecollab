"""Actual current encrypted message edits before shared stack publication."""
import time

def run_source_stage(pages,checks):
    print('Federation proof stage: actual current encrypted message edit and stack digest recheck',flush=True)
    source=pages[0].evaluate("async()=>{window.sourceOrg=(await import('/src/lib/encrypted-document-organization-api.ts')).encryptedDocumentOrganizationApi;const stack=await window.sourceOrg.createStack(window.proofChannel,'Private edited message stack',crypto.randomUUID()),message=await window.cryptoProof.sendMessage(window.proofChannel,'PRIVATE STACK ORIGINAL · Å');return {stack,id:message.id}}")
    pages[1].evaluate("async()=>{window.sourceOrg=(await import('/src/lib/encrypted-document-organization-api.ts')).encryptedDocumentOrganizationApi}")
    original=pages[1].evaluate("async id=>await window.sourceOrg.previewMessage(window.proofChannel,id)",source['id'])
    assert original['text']=='PRIVATE STACK ORIGINAL · Å' and original['edited_at'] is None
    pages[0].evaluate("async id=>await window.cryptoProof.editMessage(window.proofChannel,id,'PRIVATE STACK EDITED · 🧭')",source['id'])
    deadline=time.monotonic()+90;current=None
    while time.monotonic()<deadline:
        current=pages[1].evaluate("async id=>await window.sourceOrg.previewMessage(window.proofChannel,id)",source['id'])
        if current['text']=='PRIVATE STACK EDITED · 🧭':break
        pages[1].wait_for_timeout(300)
    else:raise AssertionError('Peer canonical verified edit did not replace its original message source')
    assert current['id']==original['id'] and current['created_at']==original['created_at'] and current['edited_at']
    assert current['expected_text_sha256']!=original['expected_text_sha256']
    denied=pages[1].evaluate("async({stack,preview})=>{try{await window.sourceOrg.saveMessage(window.proofChannel,stack,preview,crypto.randomUUID());return false}catch(error){return String(error).includes('changed')}}",{'stack':source['stack'],'preview':original})
    assert denied,'A stale reviewed original was published after a trusted edit became current'
    row=pages[1].evaluate("async({stack,preview})=>await window.sourceOrg.saveMessage(window.proofChannel,stack,preview,crypto.randomUUID())",{'stack':source['stack'],'preview':current})
    result=pages[0].evaluate("async id=>{const v=await window.sourceOrg.list(window.proofChannel);return v.message_references.find(row=>row.id===id)}",row['id'])
    assert result['available'] and result['excerpt']=='PRIVATE STACK EDITED · 🧭' and result['source_edited_at']
    checks['actual_verified_encrypted_replace_projection_rejects_stale_stack_review_and_shares_current_peer_excerpt']=True
    return ['PRIVATE STACK ORIGINAL · Å','PRIVATE STACK EDITED · 🧭']
