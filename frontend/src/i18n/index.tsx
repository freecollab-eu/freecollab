import { createContext, useCallback, useContext, useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import { api } from '../lib/api';
import { applyLocale, currentLocale, isLanguageChoice, languageChoices, registerCatalog, resolveLanguage, subscribeLocale, t, type Catalog, type LanguageChoice } from './core';
import sv from './sv';
import './language.css';

registerCatalog('sv', sv);
// Feature teams own separate catalogs; adding a catalog never requires runtime
// translation services or sending channel contents to a third party.
for (const module of Object.values(import.meta.glob<{ default: Catalog }>('./*.sv.ts', { eager: true }))) registerCatalog('sv', module.default);
export * from './core';

const key = (user: string | null) => `freecollab.language:${user || 'guest'}`;
function cached(user: string | null): LanguageChoice { try { const value = localStorage.getItem(key(user)); return isLanguageChoice(value) ? value : 'auto'; } catch { return 'auto'; } }
function cache(user: string | null, choice: LanguageChoice) { try { localStorage.setItem(key(user), choice); } catch { /* Account API remains authoritative. */ } }
interface LanguageContext { choice: LanguageChoice; saving: boolean; error: string; saved: boolean; choose: (choice: LanguageChoice) => void; retry: () => void }
const Context = createContext<LanguageContext | null>(null);

export function LocaleProvider({ children }: { children: ReactNode }) {
  const initial = cached(api.getCurrentUserId());
  const [choice, setChoice] = useState(initial), [saving, setSaving] = useState(false), [error, setError] = useState(''), [saved, setSaved] = useState(false);
  const identity = useRef(api.getCurrentUserId()), token = useRef(api.getAccessToken()), generation = useRef(0), sequence = useRef(0);
  const selected = useRef(initial), pending = useRef(false), mounted = useRef(false);
  const queues = useRef(new Map<string, Promise<void>>());
  const controller = useRef<AbortController | null>(null);
  const show = useCallback((next: LanguageChoice) => { selected.current = next; setChoice(next); applyLocale(resolveLanguage(next)); }, []);
  const load = useCallback(async () => {
    const account = identity.current, credential = token.current, version = generation.current, request = sequence.current;
    if (!account || !credential || pending.current) return;
    controller.current?.abort(); const abort = new AbortController(); controller.current = abort;
    try {
      const result = await api.request<{ locale: LanguageChoice }>('/preferences/language', { signal: abort.signal, headers: { Authorization: `Bearer ${credential}` } });
      if (!mounted.current || abort.signal.aborted || version !== generation.current || request !== sequence.current || credential !== api.getAccessToken() || pending.current) return;
      if (!isLanguageChoice(result.locale)) throw new Error('Invalid language preference');
      cache(account, result.locale); show(result.locale); setError('');
    } catch { if (mounted.current && !abort.signal.aborted && version === generation.current && request === sequence.current && !pending.current) setError('Your saved language could not be loaded.'); }
  }, [show]);
  const choose = useCallback((next: LanguageChoice) => {
    if (!isLanguageChoice(next)) return;
    const account = identity.current, credential = token.current, version = generation.current, request = ++sequence.current;
    controller.current?.abort(); show(next); setError(''); setSaved(false);
    if (!account || !credential) { cache(null, next); return; }
    pending.current = true; setSaving(true);
    // Serialize account writes, so a slow earlier selection cannot win over a
    // later selection. Auth changes invalidate queued work before it starts.
    const operation = (queues.current.get(account) || Promise.resolve()).catch(() => {}).then(async () => {
      if (!mounted.current || version !== generation.current || request !== sequence.current || credential !== api.getAccessToken()) return;
      try {
        await api.request('/preferences/language', { method: 'PUT', headers: { Authorization: `Bearer ${credential}` }, body: JSON.stringify({ locale: next }) });
        if (!mounted.current || version !== generation.current || request !== sequence.current || credential !== api.getAccessToken()) return;
        cache(account, next); pending.current = false; setSaved(true); setError('');
      } catch { if (mounted.current && version === generation.current && request === sequence.current && credential === api.getAccessToken()) setError('Language could not be saved. Try again.'); }
      finally { if (mounted.current && version === generation.current && request === sequence.current) setSaving(false); }
    });
    queues.current.set(account, operation);
    void operation.then(() => { if (queues.current.get(account) === operation) queues.current.delete(account); });
  }, [show]);
  useEffect(() => {
    mounted.current = true; show(cached(identity.current)); void load();
    const sync = () => {
      const next = api.getCurrentUserId(), credential = api.getAccessToken();
      if (next === identity.current && credential === token.current) return;
      generation.current++; sequence.current++; controller.current?.abort(); pending.current = false;
      identity.current = next; token.current = credential; setSaving(false); setError(''); setSaved(false); show(cached(next)); void load();
    };
    const storage = (event: StorageEvent) => { sync(); if (event.key === key(identity.current) && !pending.current) { show(cached(identity.current)); void load(); } };
    const browser = () => { if (selected.current === 'auto') applyLocale(resolveLanguage('auto')); };
    window.addEventListener('freecollab:auth-changed', sync); window.addEventListener('storage', storage); window.addEventListener('languagechange', browser);
    return () => { mounted.current = false; generation.current++; controller.current?.abort(); window.removeEventListener('freecollab:auth-changed', sync); window.removeEventListener('storage', storage); window.removeEventListener('languagechange', browser); };
  }, [load, show]);
  return <Context.Provider value={{ choice, saving, error, saved, choose, retry: () => { if (pending.current) choose(selected.current); else void load(); } }}>{children}</Context.Provider>;
}

export function useI18n() {
  const locale = useSyncExternalStore(subscribeLocale, currentLocale, () => 'en' as const);
  return { t, locale };
}
export function LanguagePicker({ compact = false }: { compact?: boolean }) {
  const context = useContext(Context), { t } = useI18n();
  if (!context) return null;
  return <div className={`fc-language${compact ? ' fc-language-compact' : ''}`}>
    <label><span>{t('Language')}</span><select aria-label={t('Language')} value={context.choice} onChange={event => context.choose(event.target.value as LanguageChoice)}>{languageChoices.map(language => <option key={language.id} value={language.id} lang={language.id === 'auto' ? undefined : language.id}>{language.id === 'auto' ? t(language.name) : language.name}</option>)}</select></label>
    {!compact && <p>{t('Your language follows your account on every device.')}</p>}
    <span role="status" className="fc-language-status">{context.saving ? t('Saving…') : context.saved && !compact ? t('Language saved.') : ''}</span>
    {context.error && <span role="alert">{t(context.error)} <button type="button" onClick={context.retry}>{t('Retry')}</button></span>}
  </div>;
}
