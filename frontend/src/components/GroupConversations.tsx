import { useEffect, useRef, useState } from 'react';
import { Users } from 'lucide-react';
import { api, type Channel } from '../lib/api';
import { chatProductivity, type GroupConversation } from '../lib/chat-productivity';
import { getSecureMessaging } from '../lib/secure-client';
import { type WorkspaceMember } from '../lib/workspace-api';
import { t } from '../i18n';

export function GroupConversations({workspaceId,people,onOpen}:{workspaceId:string;people:WorkspaceMember[];onOpen:(channel:Channel)=>void}) {
  const [groups,setGroups]=useState<GroupConversation[]>([]),[selected,setSelected]=useState<string[]>([]),[editing,setEditing]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const token=useRef(api.getAccessToken()),live=useRef(true),workspace=useRef(workspaceId);workspace.current=workspaceId;
  const current=()=>live.current&&workspace.current===workspaceId&&token.current===api.getAccessToken();
  useEffect(()=>{live.current=true;setGroups([]);setSelected([]);setEditing(false);setBusy(false);setError('');void chatProductivity.groups(workspaceId).then(rows=>{if(current())setGroups(rows);}).catch(failure=>{if(current())setError(failure.message);});return()=>{live.current=false;};},[workspaceId]);
  async function open(group?:GroupConversation) {if(busy)return;setBusy(true);setError('');try{
    const status=await api.request<{enabled:boolean}>('/secure/status'); if(!current())return;
    const result=group||await chatProductivity.openGroup(workspaceId,selected);if(!current())return;
    if(status.enabled&&(result.created||result.secure&&!result.matrix_room_id)){
      const secure=await getSecureMessaging();if(!current())return;
      const binding=await secure.enableChannel(result.id,result.name||'Group conversation');if(!current())return;
      result.secure=true;result.matrix_room_id=binding.room_id||undefined;
    }
    if(current()){window.dispatchEvent(new Event('freecollab:channels-updated'));onOpen(result);}
  }catch(failure){if(current()){setError(failure instanceof Error?failure.message:String(failure));void chatProductivity.groups(workspaceId).then(rows=>{if(current())setGroups(rows);}).catch(()=>{});}}finally{if(current())setBusy(false);}}
  return <section className="fc-account-section"><div className="fc-dm-heading"><h3><Users size={16}/> {t('Group conversations')}</h3><button type="button" className="fc-account-link" disabled={busy} onClick={()=>setEditing(value=>!value)}>{editing?t('Cancel'):t('New group conversation')}</button></div>{error&&<p role="alert" className="fc-alert">{error}</p>}
    {editing&&<form onSubmit={event=>{event.preventDefault();void open();}}><p>{t('Choose two to nine people. Adding someone later starts a new conversation.')}</p><fieldset disabled={busy} style={{border:0,padding:0,display:'grid',gap:12,maxHeight:220,overflow:'auto'}}><legend className="fc-visually-hidden">{t('Group participants')}</legend>{people.filter(person=>person.user_id!==api.getCurrentUserId()).map(person=><label key={person.user_id} style={{display:'flex',alignItems:'center',gap:10}}><input type="checkbox" checked={selected.includes(person.user_id)} disabled={!selected.includes(person.user_id)&&selected.length>=9} onChange={event=>setSelected(previous=>event.target.checked?[...previous,person.user_id]:previous.filter(id=>id!==person.user_id))}/>{person.name}</label>)}</fieldset><button type="submit" className="fc-button fc-button-primary" disabled={busy||selected.length<2}>{t('Open group conversation')}</button></form>}
    <ul className="fc-dm-list">{groups.map(group=><li key={group.id}><button type="button" className="fc-dm-open" disabled={busy} onClick={()=>void open(group)}><Users size={18}/><div><strong>{group.name}</strong><small>{group.participants.length} {t('participants')}</small></div></button><button className="fc-account-link" type="button" disabled={busy} onClick={()=>{if(!window.confirm(t('Leave this group conversation?')))return;setBusy(true);void chatProductivity.leaveGroup(group.id).then(()=>{if(current()){setGroups(rows=>rows.filter(row=>row.id!==group.id));window.dispatchEvent(new Event('freecollab:channels-updated'));}}).catch(failure=>{if(current())setError(failure.message);}).finally(()=>{if(current())setBusy(false);});}}>{t('Leave')}</button></li>)}</ul>
  </section>;
}
