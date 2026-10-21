import { t, useI18n } from '../i18n';
import { useEffect, useRef, useState } from 'react';
import { AlertCircle, Loader2, ShieldCheck } from 'lucide-react';
import { api } from '../lib/api';
import { oidcApi, type SSOExchange } from '../lib/integrations-api';
import { Brand } from './Brand';
import '../styles/account.css';

interface Props { code: string; onComplete: (result: SSOExchange) => void; onBack: () => void }
export function SSOCallback({ code, onComplete, onBack }: Props) {
  useI18n();
  const pending = useRef<Promise<SSOExchange> | null>(null);
  const owner = useRef(api.getAccessToken());
  const complete = useRef(onComplete); complete.current = onComplete;
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    pending.current ||= oidcApi.exchange(code);
    void pending.current.then(result => {
      if (!active) return;
      if (owner.current !== api.getAccessToken()) { setError(t("The signed-in account changed. Please start sign-in again.")); return; }
      complete.current(result);
    }).catch(failure => { if (active) setError(failure instanceof Error ? failure.message : t("Single sign-on could not be completed.")); });
    return () => { active = false; };
  }, [code]);
  return <main className="fc-authentication-flow">
    <header><Brand /><button type="button" className="fc-account-link" onClick={onBack}>{t("Back to FreeCollab")}</button></header>
    <section className="fc-authentication-card" aria-labelledby="sso-title">
      <span className="fc-account-feature-icon"><ShieldCheck size={28} /></span>
      <p className="fc-kicker">{t("Your organization, your account")}</p><h1 id="sso-title">{t("Completing sign-in.")}</h1>
      {error ? <><div className="fc-alert fc-account-error" role="alert"><AlertCircle size={17} /><p>{error}</p></div><button className="fc-button" type="button" onClick={onBack}>{t("Return to sign in")}</button></> : <p className="fc-account-loading" role="status"><Loader2 size={18} className="fc-account-spinner" />{t("Checking your identity securely…")}</p>}
    </section>
  </main>;
}
