"""Actual two-installation encrypted document organization and anchored comments."""

def run_organization_stage(pages, checks, document_id, generic):
    print('Federation proof stage: actual peer Yjs stacks, relationships and mixed collections',flush=True)
    stack=pages[0].evaluate("async()=>{window.orgProof=(await import('/src/lib/encrypted-document-organization-api.ts')).encryptedDocumentOrganizationApi;return await window.orgProof.createStack(window.proofChannel,'Secret peer document stack',crypto.randomUUID())}")
    documents=pages[1].evaluate("""async({stack,documentId})=>{window.orgProof=(await import('/src/lib/encrypted-document-organization-api.ts')).encryptedDocumentOrganizationApi;const c=window.orgProof,v=await c.list(window.proofChannel),doc=v.documents.find(d=>d.id===documentId);if(!doc?.encrypted_resource)throw Error('Peer Yjs metadata missing');await c.move(window.proofChannel,documentId,stack.id,0,stack.generation);const other=v.documents.find(d=>d.id!==documentId&&d.encrypted_resource);if(!other)throw Error('Second peer document missing');await c.relate(window.proofChannel,documentId,other.id,crypto.randomUUID());return {first:documentId,second:other.id}}""",{'stack':stack,'documentId':document_id})
    result=pages[0].evaluate("""async({stack,documents})=>{const v=await window.orgProof.list(window.proofChannel);return {moved:v.placements.some(p=>p.document_id===documents.first&&p.stack_id===stack.id),related:v.relations.some(r=>[r.first_document_id,r.second_document_id].includes(documents.first)&&[r.first_document_id,r.second_document_id].includes(documents.second))}}""",{'stack':stack,'documents':documents})
    assert result=={'moved':True,'related':True},result
    denied=pages[1].evaluate("""async stack=>{try{await window.orgProof.renameStack(window.proofChannel,stack,'Unauthorized rename');return false}catch{return true}}""",stack)
    assert denied,'A foreign member renamed another creator stack'
    collection=pages[0].evaluate("""async({documentId,project})=>{window.collectionsProof=(await import('/src/lib/encrypted-collections-api.ts')).encryptedCollectionsApi;return await window.collectionsProof.create(window.proofChannel,{title:'Secret mixed peer collection',items:[{kind:'document',id:documentId},{kind:'project',id:project}]})}""",{'documentId':document_id,'project':generic['project']})
    collection_result=pages[1].evaluate("""async({id,feed})=>{window.collectionsProof=(await import('/src/lib/encrypted-collections-api.ts')).encryptedCollectionsApi;const c=window.collectionsProof,r=(await c.list(window.proofChannel)).find(r=>r.id===id);return await c.addItem(window.proofChannel,r,{kind:'feed',id:feed})}""",{'id':collection['id'],'feed':generic['feed']})
    assert len(collection_result['items'])==3
    checks['v2_actual_peer_document_stacks_placement_relationships_and_mixed_collection_CAS']=True
    print('Federation proof stage: actual peer document passage comments and immutable creator resolution',flush=True)
    for page in pages:
        page.evaluate("""async id=>{const {React,createRoot,FederatedWorkPanel}=window.fixtureModules;window.commentUI=createRoot(document.getElementById('work-proof'));window.commentUI.render(React.createElement(FederatedWorkPanel,{channelId:window.proofChannel,initialResourceId:id,initialResourceKind:'document'}))}""",document_id)
        page.get_by_label('Document text',exact=True).wait_for(state='visible',timeout=90000)
    field=pages[0].get_by_label('Document text',exact=True)
    field.fill(field.input_value()+'\n@Secret shared')
    pages[0].get_by_role('option').filter(has_text='Secret shared project').click(timeout=90000)
    pages[0].get_by_role('button',name='Save encrypted changes',exact=True).click()
    pages[0].get_by_role('status').filter(has_text='Encrypted shared changes saved.').wait_for(timeout=90000)
    peer_reference=pages[1].evaluate("async id=>{const c=window.workProof,r=(await c.load()).records.find(r=>r.id===id&&r.kind==='document'),s=await c.document(r);return s.references.body.find(ref=>ref.kind==='resource'&&ref.resource_kind==='project')?.id}",document_id)
    assert peer_reference==generic['project']
    pages[0].evaluate("()=>window.addEventListener('freecollab:open-encrypted-resource',event=>window.lastProofReference=event.detail,{once:true})")
    pages[0].get_by_role('button',name='@Secret shared project',exact=True).click()
    assert pages[0].evaluate('()=>window.lastProofReference')=={'channel_id':pages[0].evaluate('()=>window.proofChannel'),'kind':'project','id':generic['project']}
    checks['v2_actual_cross_family_project_picker_in_peer_Yjs_and_exact_kind_navigation']=True
    pages[0].get_by_label('Document text',exact=True).evaluate('(field)=>field.setSelectionRange(3,9)')
    pages[0].get_by_role('button',name='Comment on selected passage',exact=True).click(timeout=90000)
    pages[0].get_by_label('Add a comment',exact=True).fill('Secret anchored peer comment')
    pages[0].get_by_role('button',name='Post comment',exact=True).click()
    for page in pages:
        page.get_by_text('Secret anchored peer comment',exact=True).wait_for(state='visible',timeout=90000)
    pages[1].get_by_role('button',name='View passage',exact=True).click()
    selected=pages[1].get_by_label('Document text',exact=True).evaluate('field=>field.value.slice(field.selectionStart,field.selectionEnd)')
    assert selected=='Review',selected
    forged=pages[1].evaluate("""async()=>{const m=await import('/src/lib/encrypted-work-api.ts'),c=window.encryptedWork,v=await c.load(),r=v.records.find(r=>r.kind==='comment'&&r.selected.fields.body==='Secret anchored peer comment'),sent=await window.cryptoProof.sendEvent(window.proofChannel,m.ENCRYPTED_WORK_REVISION,{protocol:m.ENCRYPTED_WORK_PROTOCOL,connection_id:v.context.connection_id,id:r.id,kind:r.kind,parents:r.heads.map(h=>h.event_id),archived:false,fields:{...r.selected.fields,resolved:true}});await m.publishEncryptedRoomPointer(window.proofChannel,sent.event_id);return {id:r.id,event:sent.event_id}}""")
    status=pages[0].evaluate("async f=>{const r=(await window.encryptedWork.load()).records.find(r=>r.id===f.id&&r.kind==='comment');return {resolved:r.selected.fields.resolved,invalid:r.invalid.length,quarantined:r.quarantined.includes(f.event)}}",forged)
    assert status=={'resolved':False,'invalid':0,'quarantined':True},status
    pages[0].get_by_role('button',name='Resolve comment',exact=True).click()
    pages[0].get_by_role('button',name='Reopen comment',exact=True).wait_for(state='visible',timeout=90000)
    assert pages[1].evaluate("async id=>(await window.encryptedWork.load()).records.find(r=>r.id===id&&r.kind==='comment').selected.fields.resolved",forged['id'])
    for page in pages:page.evaluate('()=>window.commentUI.unmount()')
    checks['v2_actual_selected_passage_UI_peer_Yjs_anchor_read_and_creator_only_immutable_resolution']=True
    return ['Secret peer document stack','Secret mixed peer collection','Secret anchored peer comment']
