import {useEffect,useRef,useState} from 'react';
import {api} from '../lib/api';
import {t} from '../i18n';
import type {SharedDocumentRecord} from '../lib/shared-documents-api';
import type {DocumentRelation,StackDocumentComment} from '../lib/document-organization-api';
import type {SearchSource} from '../lib/enterprise-search';

/** Optional automatic local proposals; only explicit Save creates a relation. */
export function SemanticDocumentSuggestions({channelId,document:source,documents,comments,relations,busy,onSave,onOpen}:{channelId:string;document:SharedDocumentRecord;documents:SharedDocumentRecord[];comments:StackDocumentComment[];relations:DocumentRelation[];busy:boolean;onSave:(id:string)=>void;onOpen:(document:SharedDocumentRecord,commentId:string)=>void}){
 const actor=api.getCurrentUserId(),credential=api.getAccessToken(),preference='freecollab-semantic-relations:'+actor;
 const initial=()=>{try{return !api.isMemorySession()&&localStorage.getItem(preference)==='true';}catch{return false;}};
 const [enabled,setEnabled]=useState(initial),[hits,setHits]=useState<Array<SearchSource&{score:number}>>([]),[working,setWorking]=useState(false),[error,setError]=useState('');
 const epoch=useRef(0),region=useRef<HTMLElement>(null);
 const corpus=documents.filter(row=>row.channel_id===channelId&&!row.archived&&!row.resolution_error).slice(0,1000).map(row=>({id:row.id,channel_id:channelId,kind:'document' as const,title:row.title||'',text:comments.filter(comment=>comment.channel_id===channelId&&comment.document_id===row.id&&!comment.resolved).map(comment=>comment.text).join('\n'),created_by:row.created_by,created_at:row.created_at,parent_id:null,truncated:false,version:JSON.stringify([row.encrypted_source_version,row.update_count,comments.filter(comment=>comment.document_id===row.id).map(comment=>[comment.id,comment.version,comment.resolved])])}));
 const identity=JSON.stringify([actor,credential,channelId,source.id,corpus,relations.map(row=>[row.first_document_id,row.second_document_id])]);
 useEffect(()=>{setEnabled(initial());setHits([]);},[actor,credential]);
 useEffect(()=>{
  const request=++epoch.current;setHits([]);setError('');setWorking(false);
  if(!enabled||corpus.length<4||region.current?.closest('[hidden],[inert]'))return;
  const selected=corpus.find(row=>row.id===source.id);if(!selected)return;
  const worker=new Worker(new URL('../lib/semantic-search.worker.ts',import.meta.url),{type:'module'});setWorking(true);
  const current=()=>request===epoch.current&&actor===api.getCurrentUserId()&&credential===api.getAccessToken();
  worker.onmessage=event=>{if(!current())return;setWorking(false);if(event.data.error){setError('Local meaning-based suggestions are unavailable.');return;}setHits((event.data.hits as Array<SearchSource&{score:number}>).filter(row=>row.id!==source.id&&row.score>=.35&&!relations.some(relation=>[relation.first_document_id,relation.second_document_id].includes(source.id)&&[relation.first_document_id,relation.second_document_id].includes(row.id))).slice(0,3));};
  worker.onerror=()=>{if(current()){setWorking(false);setError('Local meaning-based suggestions are unavailable.');}};
  worker.postMessage({items:corpus,query:selected.title+'\n'+selected.text});
  return()=>{++epoch.current;worker.terminate();};
 },[identity,enabled]);
 function toggle(value:boolean){setEnabled(value);try{if(!api.isMemorySession())localStorage.setItem(preference,String(value));}catch{/* A nonsecret device preference may remain in memory. */}}
 return <section ref={region} className="fc-document-proposals" aria-label={t('Meaning-based document connections')}><label><input type="checkbox" checked={enabled} onChange={event=>toggle(event.target.checked)} />{t('Automatically suggest document connections on this device')}</label>{enabled&&<><p className="fc-shared-muted">{t('A local latent-semantic model uses current visible page titles and unresolved comments. It recomputes as discussion changes. Review before saving; no AI provider receives content.')}</p>{corpus.length<4&&<p className="fc-shared-muted">{t('Add context to at least four pages for meaning-based proposals. Direct word suggestions remain below.')}</p>}{working&&<p role="status">{t('Finding local connections…')}</p>}{error&&<p role="status">{t(error)}</p>}{!working&&corpus.length>=4&&!hits.length&&!error&&<p className="fc-shared-muted">{t('No strong unsaved connections in the current context.')}</p>}{hits.map(hit=>{const target=documents.find(row=>row.id===hit.id)!;const evidence=comments.filter(comment=>comment.channel_id===channelId&&!comment.resolved&&[source.id,target.id].includes(comment.document_id)).slice(0,3);return <div key={hit.id}><strong>{target.title||t('Untitled document')}</strong><small>{t('Meaning similarity: {score}%',{score:Math.round(hit.score*100)})}</small>{evidence.map(comment=><button key={comment.id} type="button" className="fc-shared-text-button" onClick={()=>onOpen(documents.find(row=>row.id===comment.document_id)!,comment.id)}>{t('View source comment')}</button>)}<button type="button" className="fc-button" disabled={busy} onClick={()=>onSave(target.id)}>{t('Save relation')}</button></div>;})}</>}</section>;
}
