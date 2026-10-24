import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Bell, Check, Clock, RefreshCw, X } from 'lucide-react';
import { api, type Channel, type Message } from '../lib/api';
import type { MessageReference } from '../lib/message-references';
import { chatProductivity, type MessageReminder, type ScheduledMessage } from '../lib/chat-productivity';
import { mobileOutbox, type QueuedMessage } from '../mobile';
import { useModalFocus } from '../lib/useModalFocus';
import { t, useI18n, currentLocale } from '../i18n';
import '../styles/chat-productivity.css';

interface Props {channel:Channel;draft?:string;mentions?:string[];references?:MessageReference[];message?:Message;onScheduled?:()=>void;onClose:()=>void}
const initialTime = () => {const value=new Date(Date.now()+3600000);value.setMinutes(value.getMinutes()-value.getTimezoneOffset());return value.toISOString().slice(0,16);};
export function ChatProductivityPanel({channel,draft='',mentions=[],references=[],message,onScheduled,onClose}:Props) {
  useI18n();
  const dialog=useRef<HTMLElement>(null), token=useRef(api.getAccessToken()),live=useRef(true);
  const [due,setDue]=useState(initialTime),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const [scheduled,setScheduled]=useState<ScheduledMessage[]>([]),[local,setLocal]=useState<QueuedMessage[]>([]),[reminders,setReminders]=useState<MessageReminder[]>([]);
  const current=()=>live.current&&token.current===api.getAccessToken();
  const close=()=>{if(!busy)onClose();}; useModalFocus(dialog,close);
  useEffect(()=>{live.current=true;void load().catch(failure=>{if(current())setError(String(failure.message||failure));});return()=>{live.current=false;};},[channel.id]);
  async function load() {
    const [server,client,notices]=await Promise.all([channel.secure?Promise.resolve([]):chatProductivity.scheduled(channel.id),mobileOutbox.list(),chatProductivity.reminders(channel.id)]);
    if(current()){setScheduled(server);setLocal(client.filter(item=>item.channelId===channel.id&&item.sendAt));setReminders(notices);}
  }
  async function run(action:()=>Promise<void>) {if(busy)return;setBusy(true);setError('');try{await action();if(current())await load();}catch(failure){if(current())setError(failure instanceof Error?failure.message:String(failure));}finally{if(current())setBusy(false);}}
  const label=(value:string)=>new Date(value).toLocaleString(currentLocale(),{dateStyle:'medium',timeStyle:'short'});
  return createPortal(<div className="fc-account-overlay"><section ref={dialog} role="dialog" aria-modal="true" aria-label={t('Scheduled messages and reminders')} tabIndex={-1} className="fc-account-panel fc-productivity-panel">
    <header className="fc-account-header"><div><span className="fc-kicker">{channel.name}</span><h2>{message?t('Come back to this.'):t('A little later.')}</h2><p>{t('Give the conversation the right moment.')}</p></div><button className="fc-icon-button" type="button" aria-label={t('Close scheduling')} disabled={busy} onClick={close}><X size={20}/></button></header>
    <div className="fc-account-body">{error&&<p className="fc-alert" role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
      {(message||draft.trim())&&<form className="fc-productivity-form" onSubmit={event=>{event.preventDefault();void run(async()=>{
        const dueAt=new Date(due).toISOString();
        if(message)await chatProductivity.remind(channel.id,message.id,dueAt);
        else if(channel.secure)await mobileOutbox.schedule(channel.id,draft,mentions,dueAt,references);
        else await chatProductivity.schedule(channel.id,draft,mentions,dueAt,references);
        if(current()){setNotice(message?t('Reminder saved.'):t('Message scheduled.'));if(!message)onScheduled?.();}
      });}}><blockquote>{message?.content||draft}</blockquote><label htmlFor="chat-schedule-time">{t('When?')}</label><input id="chat-schedule-time" className="fc-input" type="datetime-local" value={due} required disabled={busy} onChange={event=>setDue(event.target.value)}/>
        <p>{channel.secure&&!message?t('This message stays encrypted on this device. Keep the app connected and this sign-in active for delivery.'):t('The server checks your sign-in and channel access again before delivery. Signing out cancels pending delivery.')}</p>
        <button type="submit" className="fc-button fc-button-primary" disabled={busy}><Clock size={16}/>{message?t('Remind me'):t('Schedule message')}</button></form>}
      <div className="fc-dm-heading"><h3>{t('Scheduled messages')}</h3><button type="button" className="fc-icon-button" disabled={busy} aria-label={t('Refresh scheduled messages')} onClick={()=>void run(load)}><RefreshCw size={16}/></button></div>
      {!scheduled.length&&!local.length&&<p>{t('No scheduled messages in this conversation.')}</p>}
      <ul className="fc-productivity-list">{scheduled.map(item=><li key={item.id}><Clock size={16}/><div><time>{label(item.due_at)}</time><p>{item.content||(({sent:t('Sent'),cancelled:t('Cancelled')} as Record<string,string>)[item.status]||item.status)}</p>{item.failure_code&&<small>{t('Delivery cancelled because access or policy changed.')}</small>}</div>{item.status==='pending'&&<button className="fc-button" type="button" disabled={busy} onClick={()=>void run(async()=>{await chatProductivity.cancel(item.id);})}>{t('Cancel')}</button>}</li>)}
      {local.map(item=><li key={item.id}><Clock size={16}/><div><time>{label(item.sendAt!)}</time><p>{item.content}</p><small>{item.state==='review'?t('Review required in Mobile & offline'):t('On this device')}</small></div><button type="button" className="fc-button" disabled={busy} onClick={()=>void run(async()=>{await mobileOutbox.remove(item.id);})}>{t('Cancel')}</button></li>)}</ul>
      <h3>{t('Your reminders')}</h3><ul className="fc-productivity-list">{reminders.map(item=><li key={item.id}><Bell size={16}/><div><time>{label(item.due_at)}</time><p>{(({pending:t('Pending'),notified:t('Ready to revisit'),completed:t('Completed'),cancelled:t('Cancelled')} as Record<string,string>)[item.status]||item.status)}</p></div>{['pending','notified'].includes(item.status)&&<><button type="button" className="fc-button" disabled={busy} onClick={()=>void run(async()=>{await chatProductivity.snoozeReminder(item.id,new Date(Date.now()+3600000).toISOString());})}>{t('In one hour')}</button><button type="button" className="fc-icon-button" aria-label={t('Complete reminder')} disabled={busy} onClick={()=>void run(async()=>{await chatProductivity.completeReminder(item.id);})}><Check size={18}/></button></>}</li>)}</ul>
    </div><footer className="fc-account-footer"><Clock size={16}/><span>{t('Times use your local timezone. Up to seven days ahead.')}</span><button type="button" className="fc-button" disabled={busy} onClick={close}>{t('Done')}</button></footer>
  </section></div>,document.body);
}
