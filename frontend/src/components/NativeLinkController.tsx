import { useEffect, useRef } from 'react';
import { api } from '../lib/api';
import { authorizeNativeSource, clearNativeSource, nativeSourceFromLocation, parseNativeLink, pendingNativeSource, rememberNativeSource, type NativeOpenSource } from '../lib/native-links';
interface LinkPlugin { getPendingLink(): Promise<{ url?: string; generation?: string }>; clearPendingLink(value: { url: string; generation: string }): Promise<void> }
/** Links are untrusted source hints. A newer OS/browser intent cancels every old await. */
export function NativeLinkController({ onOpen, onError }: { onOpen: (source: NativeOpenSource, isCurrent: () => boolean) => Promise<boolean>; onError?: (message: string) => void }) {
  const callbacks = useRef({ onOpen, onError }); callbacks.current = { onOpen, onError };
  useEffect(() => {
    let live=true, reading=false, opening=false, observed='', attempted='', epoch=0, timer:ReturnType<typeof setTimeout>;
    let plugin = window.Capacitor?.isNativePlatform?.() && window.Capacitor.registerPlugin
      ? window.Capacitor.registerPlugin('FreeCollabNative') as unknown as LinkPlugin : null;
    async function inspect() {
      if (!live || reading) return; reading=true;
      try {
        const token=api.getAccessToken(),actor=api.getCurrentUserId(); if(!token||!actor)return;
        let pending:{url?:string;generation?:string}={};
        if(plugin)try{pending=await plugin.getPendingLink();}catch{plugin=null;callbacks.current.onError?.('Update the native client to receive verified app links.');}
        if(!live||token!==api.getAccessToken()||actor!==api.getCurrentUserId())return;
        const fragment=location.pathname==='/open'||location.hash.startsWith('#open=')?location.href:'';
        let hint=pendingNativeSource();
        if(fragment){const source=nativeSourceFromLocation();if(source&&(!hint||JSON.stringify(source)!==JSON.stringify(hint.source)))hint=rememberNativeSource(source);}
        if(pending.url&&(typeof pending.generation!=='string'||!/^[A-Fa-f0-9-]{36}$/.test(pending.generation)))throw new Error('Invalid native app-link generation.');
        const key=pending.url?pending.url+'\0'+pending.generation:hint?'browser\0'+hint.generation:'';
        if(!key)return;
        if(key!==observed){observed=key;++epoch;}
        // Keep polling metadata while a resource read/dirty guard is awaiting.
        // This invalidates its lease before it can finish with an older source.
        if(opening||attempted===key)return;
        attempted=key; const attempt=epoch,source=pending.url?parseNativeLink(pending.url):hint?.source;
        if(!source)return;
        const isCurrent=()=>live&&attempt===epoch&&token===api.getAccessToken()&&actor===api.getCurrentUserId();
        opening=true;
        void (async()=>{
          try {
            await authorizeNativeSource(source); if(!isCurrent())return;
            const accepted=await callbacks.current.onOpen(source,isCurrent); if(!accepted||!isCurrent())return;
            if(pending.url&&pending.generation)await plugin?.clearPendingLink({url:pending.url,generation:pending.generation});
            if(!isCurrent())return;
            if(!pending.url&&hint)clearNativeSource(hint);
            if(fragment&&location.href===fragment)history.replaceState(null,'','/');
          } catch(error){if(isCurrent())callbacks.current.onError?.(error instanceof Error?error.message:'The app link could not be opened.');}
          finally{opening=false;if(live)queueMicrotask(()=>void inspect());}
        })();
      } catch(error){if(live)callbacks.current.onError?.(error instanceof Error?error.message:'The app link could not be opened.');}
      finally{reading=false;if(live){clearTimeout(timer);timer=setTimeout(()=>void inspect(),1000);}}
    }
    const changed=()=>{++epoch;observed='';attempted='';void inspect();};
    window.addEventListener('freecollab:auth-changed',changed);window.addEventListener('freecollab:session-expired',changed);window.addEventListener('hashchange',changed);void inspect();
    return()=>{live=false;++epoch;clearTimeout(timer);window.removeEventListener('freecollab:auth-changed',changed);window.removeEventListener('freecollab:session-expired',changed);window.removeEventListener('hashchange',changed);};
  },[]);
  return null;
}
