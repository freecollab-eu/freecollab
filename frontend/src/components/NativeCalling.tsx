import { useEffect, useRef, useState } from 'react';
import { Mic, MicOff, Phone, PhoneOff, ShieldCheck } from 'lucide-react';
import { api, type Channel } from '../lib/api';
import { acknowledgeNativeAudio, enableNativeCalling, nativeCallingBridge, type NativeCallState } from '../mobile/native-calling';
import { t, useI18n } from '../i18n';

export function NativeCallingSettings({channel}:{channel?:Channel}) {
  useI18n();
  const [enabled,setEnabled]=useState(false),[state,setState]=useState<NativeCallState|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const [ack,setAck]=useState(false),identity=useRef(api.getAccessToken());
  const plugin=nativeCallingBridge();
  async function load(){const token=api.getAccessToken();try{const config=await api.request<{enabled:boolean}>('/mobile/native/calling/config');const current=plugin?await plugin.getCallState():null;if(token===api.getAccessToken()){setEnabled(config.enabled);setState(current);}}catch{setEnabled(false);setState(null);}}
  useEffect(()=>{const update=()=>{if(identity.current!==api.getAccessToken()){identity.current=api.getAccessToken();setState(null);setAck(false);setError('');setNotice('');}void load();};void load();window.addEventListener('freecollab:mobile-state',update);window.addEventListener('freecollab:auth-changed',update);return()=>{window.removeEventListener('freecollab:mobile-state',update);window.removeEventListener('freecollab:auth-changed',update);};},[]);
  useEffect(()=>{setAck(false);setNotice('');},[channel?.id]);
  async function run(operation:()=>Promise<unknown>){if(busy)return;setBusy(true);setError('');setNotice('');const token=api.getAccessToken();try{await operation();if(token===api.getAccessToken())await load();}catch(failure){if(token===api.getAccessToken())setError(failure instanceof Error?t(failure.message):t('Native calling could not be updated.'));}finally{setBusy(false);}}
  return <section className="fc-account-section"><div className="fc-account-section-heading"><h3>{t('Native audio calls')}</h3><Phone size={20}/></div>
    <p className="fc-account-muted">{t('Answer with the phone controls and keep audio connected in the background. Answering is your consent to join; ringing never starts your microphone.')}</p>
    <p className="fc-account-muted">{t('Native audio uses your operator’s LiveKit service. Audio is encrypted to that server; the operator can access it. Matrix message encryption is unchanged.')}</p>
    {!plugin&&<p className="fc-account-muted">{t('Open the connected Android or iOS app to use native calling.')}</p>}
    {!enabled&&<p className="fc-account-muted">{t('The operator must enable native calling and configure their LiveKit, Apple or Firebase services first.')}</p>}
    <div className="fc-account-actions"><button className="fc-button fc-button-primary" type="button" disabled={busy||!enabled||!plugin||!navigator.onLine} onClick={()=>void run(enableNativeCalling)}><Phone size={16}/>{state?.configured?t('Reconnect native calling'):t('Enable native calling')}</button>{state?.configured&&<button className="fc-button" type="button" disabled={busy} onClick={()=>void run(async()=>{await plugin?.disableCalling();window.dispatchEvent(new Event('freecollab:mobile-state'));})}>{t('Disable native calling')}</button>}</div>
    {channel&&state?.configured&&<div><p className="fc-account-muted">{t('Encrypted channels require a separate audio transport acknowledgement on each device. A changed encrypted room requires review again.')}</p><label className="fc-account-checkbox"><input type="checkbox" checked={ack} onChange={event=>setAck(event.target.checked)} disabled={busy}/>{t('I understand that native audio in this channel is accessible to the operator’s LiveKit server.')}</label><button className="fc-button" type="button" disabled={!ack||busy||!navigator.onLine} onClick={()=>void run(async()=>{await acknowledgeNativeAudio(channel);setNotice(t('Native audio transport acknowledged for this channel and device.'));})}><ShieldCheck size={16}/>{t('Allow native audio for this channel')}</button></div>}
    {error&&<p role="alert" className="fc-alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
  </section>;
}

/** Poll only OS call status; this component never joins media or activates it. */
export function NativeCallingController(){
  useI18n();const [state,setState]=useState<NativeCallState|null>(null),[error,setError]=useState('');const identity=useRef(api.getAccessToken());
  async function control(operation:()=>Promise<void>|undefined){const token=api.getAccessToken();setError('');try{await operation();}catch{if(token===api.getAccessToken())setError(t('Native call controls are unavailable.'));}}
  useEffect(()=>{const plugin=nativeCallingBridge();if(!plugin)return;let active=true,inFlight=false;
    const update=async()=>{if(inFlight)return;inFlight=true;const token=api.getAccessToken();try{const next=await plugin.getCallState();if(!active||token!==api.getAccessToken())return;
      if(next.configured){if(!token){await plugin.disableCalling();setState(null);return;}const devices=await api.request<{items:{id:string}[]}>('/mobile/native/calling/device');if(!active||token!==api.getAccessToken())return;if(!devices.items.some(row=>row.id===next.device_id)){await plugin.disableCalling();setState(null);return;}}
      if(active&&token===api.getAccessToken())setState(next);
    }catch{ /* Native watchdog independently closes stale or revoked media. */ }finally{inFlight=false;}};
    const changed=()=>{if(identity.current!==api.getAccessToken()){identity.current=api.getAccessToken();setState(null);setError('');void plugin.disableCalling().catch(()=>{});}void update();};
    void update();const timer=window.setInterval(()=>void update(),2000);window.addEventListener('freecollab:auth-changed',changed);window.addEventListener('storage',changed);window.addEventListener('freecollab:mobile-state',changed);return()=>{active=false;clearInterval(timer);window.removeEventListener('freecollab:auth-changed',changed);window.removeEventListener('storage',changed);window.removeEventListener('freecollab:mobile-state',changed);};
  },[]);
  if(!state||state.phase==='idle')return null;
  return <aside className="fc-native-signin" role="status"><Phone size={18}/><span>{state.phase==='ringing'?t('Incoming FreeCollab call'):state.phase==='connecting'?t('Connecting native audio…'):state.held?t('Call on hold'):t('Native audio call active')}</span>{state.phase==='ringing'&&<button type="button" className="fc-button fc-button-primary" onClick={()=>void control(()=>nativeCallingBridge()?.answerCall({explicit_answer:true}))}><Phone size={16}/>{t('Answer call')}</button>}{state.phase==='active'&&<button type="button" className="fc-button" onClick={()=>void control(()=>nativeCallingBridge()?.muteCall({muted:!state.muted}))}>{state.muted?<MicOff size={16}/>:<Mic size={16}/>} {state.muted?t('Unmute'):t('Mute')}</button>}<button type="button" className="fc-button" onClick={()=>void control(()=>nativeCallingBridge()?.endCall())}><PhoneOff size={16}/>{t('End call')}</button>{error&&<span role="alert">{error}</span>}</aside>;
}
export function NativeCallJoinButton({roomId,channelId}:{roomId:string;channelId:string}){
  useI18n();const [ready,setReady]=useState(false),[error,setError]=useState('');
  useEffect(()=>{let active=true;const plugin=nativeCallingBridge();if(plugin)void Promise.all([api.request<{enabled:boolean}>('/mobile/native/calling/config'),plugin.getCallState()]).then(([config,state])=>{if(active)setReady(config.enabled&&state.configured);}).catch(()=>{});return()=>{active=false;};},[roomId,channelId]);
  if(!ready)return null;return <><button className="fc-button" type="button" onClick={()=>{setError('');void nativeCallingBridge()?.joinCall({room_id:roomId,explicit_answer:true}).catch(()=>setError(t('Native call could not be started. Check device setup and channel audio consent.')));}}><Phone size={16}/>{t('Join with phone audio')}</button>{error&&<p className="fc-alert" role="alert">{error}</p>}</>;
}
