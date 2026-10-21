import { t, useI18n } from '../i18n';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { createPortal } from 'react-dom';
import { AlertCircle, Check, CheckCircle2, Copy, Download, KeyRound, Laptop, Loader2, Lock, RefreshCw, ShieldCheck, Trash2, X } from 'lucide-react';
import { api, APIError } from '../lib/api';
import type { SecureDevice } from '../lib/secure';
import { getSecureMessaging } from '../lib/secure-client';
import { useModalFocus } from '../lib/useModalFocus';
import '../styles/secure-settings.css';

interface SecureMessagingPanelProps { channelId?: string; onClose: () => void; }
interface RecoveryStatus { ready: boolean; unlocked: boolean; device_id: string | null; fingerprint: string; }
const describe = (failure: unknown) => failure instanceof Error ? failure.message : 'Please try again in a moment.';
const formatFingerprint = (value: string) => value.match(/.{1,4}/g)?.join(' ') || value;
const adapter = getSecureMessaging;

export function SecureMessagingPanel({ channelId, onClose }: SecureMessagingPanelProps) {
  useI18n();
  const dialog = useRef<HTMLElement>(null);
  const errorRef = useRef<HTMLDivElement>(null);
  const fingerprintInput = useRef<HTMLInputElement>(null);
  const revokePasswordInput = useRef<HTMLInputElement>(null);
  const [section, setSection] = useState<'recovery' | 'devices'>('recovery');
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [status, setStatus] = useState<RecoveryStatus | null>(null);
  const [devices, setDevices] = useState<SecureDevice[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [generatedKey, setGeneratedKey] = useState('');
  const [keyStored, setKeyStored] = useState(false);
  const [recoveryKey, setRecoveryKey] = useState('');
  const [verifying, setVerifying] = useState<SecureDevice | null>(null);
  const [fingerprint, setFingerprint] = useState('');
  const [compared, setCompared] = useState(false);
  const [mfaEnabled, setMFAEnabled] = useState(false);
  const [requiresUnlock, setRequiresUnlock] = useState(false);
  const [workspaceScoped, setWorkspaceScoped] = useState(false);
  const [managedAccount, setManagedAccount] = useState(false);
  const [unlockPassword, setUnlockPassword] = useState('');
  const [unlockCode, setUnlockCode] = useState('');
  const [revoking, setRevoking] = useState<SecureDevice | null>(null);
  const [revokePassword, setRevokePassword] = useState('');
  const [revokeCode, setRevokeCode] = useState('');
  const [revokeConfirmed, setRevokeConfirmed] = useState(false);
  const identity = useRef(api.getCurrentUserId());
  const session = useRef(api.getAccessToken());
  const mounted = useRef(true);
  const generation = useRef(0);
  const current = () => mounted.current && identity.current === api.getCurrentUserId() && session.current === api.getAccessToken();
  const close = () => {
    if (busy === 'revoke') return;
    if (generatedKey && !keyStored) { setSection('recovery'); setNotice(t("Save your encryption recovery key, then confirm that you have stored it.")); return; }
    onClose();
  };
  useModalFocus(dialog, close);
  useEffect(() => { if (error) errorRef.current?.focus(); }, [error]);
  useEffect(() => { if (verifying) fingerprintInput.current?.focus(); }, [verifying]);
  useEffect(() => { if (revoking) revokePasswordInput.current?.focus(); }, [revoking]);

  useEffect(() => { mounted.current = true; void load(); return () => { mounted.current = false; generation.current += 1; }; }, [channelId]);

  async function load() {
    const request = ++generation.current;
    setLoading(true); setError('');
    try {
      const [available, me] = await Promise.all([api.request<{ enabled: boolean; requires_encryption_unlock: boolean; mfa_required: boolean }>('/secure/status'), api.getMe()]);
      if (!current() || request !== generation.current) return;
      setEnabled(available.enabled); setMFAEnabled(available.mfa_required); setWorkspaceScoped(me.session_scope === 'workspace');
      setManagedAccount(me.account_type === 'managed');
      setRequiresUnlock(available.requires_encryption_unlock);
      if (!available.enabled) return;
      if (available.requires_encryption_unlock) { setStatus(null); setDevices([]); return; }
      const secure = await adapter();
      if (!current() || request !== generation.current) return;
      const [recovery, records] = await Promise.all([secure.recoveryStatus(), secure.getDevices(channelId)]);
      if (!current() || request !== generation.current) return;
      setStatus(recovery); setDevices(records);
    } catch (failure) { if (current() && request === generation.current) { if (failure instanceof APIError && failure.code === 'requires_encryption_unlock') { setRequiresUnlock(true); setStatus(null); setDevices([]); } else setError(describe(failure)); } }
    finally { if (current() && request === generation.current) setLoading(false); }
  }

  async function run(key: string, operation: () => Promise<void>) {
    if (busy) return;
    setBusy(key); setError(''); setNotice('');
    try { await operation(); }
    catch (failure) { if (current()) { if (failure instanceof APIError && failure.code === 'requires_encryption_unlock') { setRequiresUnlock(true); setStatus(null); setDevices([]); } setError(describe(failure)); } }
    finally { if (current()) setBusy(''); }
  }

  async function unlockEncryption(event: FormEvent) {
    event.preventDefault();
    await run('encryption-unlock', async () => {
      await api.request('/secure/unlock', { method: 'POST', body: JSON.stringify({ password: unlockPassword, code: unlockCode.trim() || undefined }) });
      if (!current()) return;
      setUnlockPassword(''); setUnlockCode(''); setRequiresUnlock(false);
      await load();
      if (current()) { setNotice(t("Encryption access unlocked for up to 30 minutes. This session still belongs to your workspace.")); window.dispatchEvent(new Event('freecollab:encryption-unlocked')); }
    });
  }

  async function setup() {
    await run('setup', async () => {
      const secure = await adapter();
      if (!current()) return;
      const key = await secure.setupRecovery();
      if (!current()) return;
      setGeneratedKey(key); setKeyStored(false);
      setStatus(previous => previous ? { ...previous, ready: true, unlocked: true } : previous);
      setNotice(t("Encryption recovery is ready. This key is shown once; save it before closing."));
    });
  }

  async function unlock(event: FormEvent) {
    event.preventDefault();
    await run('unlock', async () => {
      const secure = await adapter();
      if (!current()) return;
      await secure.unlockRecovery(recoveryKey.trim());
      if (!current()) return;
      setRecoveryKey(''); setStatus(previous => previous ? { ...previous, unlocked: true } : previous);
      setNotice(t("Encrypted history recovery is unlocked on this device."));
    });
  }

  async function verify(event: FormEvent) {
    event.preventDefault();
    if (!verifying || !compared) return;
    const device = verifying;
    await run('verify', async () => {
      const secure = await adapter();
      if (!current()) return;
      await secure.verifyDevice(device, fingerprint);
      if (!current()) return;
      setDevices(previous => previous.map(item => item.device_id === device.device_id && item.matrix_user_id === device.matrix_user_id ? { ...item, verified: true } : item));
      setVerifying(null); setFingerprint(''); setCompared(false); setNotice(t("Device verified. Only verify fingerprints you have checked through a trusted channel."));
    });
  }

  function chooseRevocation(device: SecureDevice | null) {
    setRevoking(device); setRevokePassword(''); setRevokeCode(''); setRevokeConfirmed(false);
    setVerifying(null); setFingerprint(''); setCompared(false); setError(''); setNotice('');
  }

  async function revokeDevice(event: FormEvent) {
    event.preventDefault();
    if (!revoking || !revokeConfirmed || revoking.user_id !== identity.current) return;
    const device = revoking;
    const requestToken = api.getAccessToken();
    const sameSession = () => current() && requestToken === api.getAccessToken();
    await run('revoke', async () => {
      const secure = await adapter();
      if (!sameSession()) return;
      const result = await secure.revokeDevice(device.device_id, revokePassword, revokeCode.trim() || undefined, managedAccount);
      if (!sameSession()) return;
      if (result.current) {
        secure.stop(); api.logout();
        window.dispatchEvent(new CustomEvent('freecollab:signed-out', { detail: { notice: 'This encryption device and its linked session have been revoked. Sign in again to create a new trusted device.' } }));
        return;
      }
      setDevices(previous => previous.filter(item => item.device_id !== device.device_id || item.user_id !== device.user_id));
      setRevoking(null); setRevokePassword(''); setRevokeCode(''); setRevokeConfirmed(false);
      setNotice(t("Device revoked and its linked session signed out. It will not receive new encrypted message keys. Messages already stored on that device cannot be taken back."));
    });
  }

  async function copy(value: string, success: string) {
    try { await navigator.clipboard.writeText(value); if (current()) setNotice(success); }
    catch { if (current()) setNotice(t("Select and copy the text below.")); }
  }

  function downloadKey() {
    const url = URL.createObjectURL(new Blob([`FreeCollab encryption recovery key\n\n${generatedKey}\n\nKeep this key private. It unlocks encrypted history; it does not sign you into your account.\n`], { type: 'text/plain;charset=utf-8' }));
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'freecollab-encryption-recovery-key.txt'; anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    setNotice(t("Recovery key downloaded. Keep the file somewhere private and safe."));
  }

  return createPortal(<div className="fc-account-overlay" onMouseDown={event => { if (event.target === event.currentTarget) close(); }}>
    <section ref={dialog} className="fc-account-panel fc-secure-panel" role="dialog" aria-modal="true" aria-label={t("Encryption devices and recovery")} tabIndex={-1}>
      <header className="fc-account-header"><div><span className="fc-kicker">{t("End-to-end encryption")}</span><h2>{t("Your words stay yours.")}</h2><p>{t("Trusted devices and encrypted history recovery.")}</p></div><button type="button" data-autofocus className="fc-icon-button" aria-label={t("Close encryption settings")} disabled={busy === 'revoke'} onClick={close}><X size={20} /></button></header>
      <nav className="fc-account-tabs" aria-label={t("Encryption sections")}><button type="button" aria-current={section === 'recovery' ? 'page' : undefined} onClick={() => setSection('recovery')}><KeyRound size={16} />{t("Recovery")}</button><button type="button" aria-current={section === 'devices' ? 'page' : undefined} onClick={() => setSection('devices')}><Laptop size={16} />{channelId ? t("Channel devices") : t("Your devices")}</button></nav>
      <div className="fc-account-body">
        {error && <div ref={errorRef} className="fc-alert fc-account-error" role="alert" tabIndex={-1}><AlertCircle size={17} /><p>{error}</p></div>}{notice && <div className="fc-alert fc-account-notice" role="status"><CheckCircle2 size={17} /><p>{notice}</p></div>}
        {loading ? <p className="fc-account-loading" role="status"><Loader2 size={19} className="fc-account-spinner" />{t("Connecting to your encryption device…")}</p> : enabled === false ? <section className="fc-account-section"><ShieldCheck size={25} /><h3>{t("Encrypted messaging is not available on this server yet.")}</h3><p className="fc-account-muted">{t("A server administrator needs to connect the encryption service before you can set up trusted devices and encrypted history recovery.")}</p></section> : requiresUnlock ? <section className="fc-account-section"><span className="fc-kicker">{t("Your personal encryption keys")}</span><h3>{t("Unlock encrypted messaging")}</h3><p className="fc-account-muted">{t("Your organization signs you into this workspace. Your encryption identity belongs to your personal FreeCollab account, so confirm your password to use its trusted devices and encrypted backup. Access lasts up to 30 minutes; your session stays within this workspace.")}</p><form className="fc-account-reauth" onSubmit={unlockEncryption} aria-label={t("Unlock encrypted messaging")}><label htmlFor="secure-unlock-password">{t("FreeCollab password")}</label><input id="secure-unlock-password" className="fc-input" type="password" autoComplete="current-password" value={unlockPassword} onChange={event => setUnlockPassword(event.target.value)} disabled={!!busy} required />{mfaEnabled && <><label htmlFor="secure-unlock-code">{t("Authentication or recovery code")}</label><input id="secure-unlock-code" className="fc-input" type="text" autoComplete="one-time-code" value={unlockCode} onChange={event => setUnlockCode(event.target.value)} disabled={!!busy} required /></>}<button type="submit" className="fc-button fc-button-primary" disabled={!!busy}>{busy === 'encryption-unlock' ? <Loader2 size={16} className="fc-account-spinner" /> : <Lock size={16} />}{t("Unlock encrypted messaging")}</button></form><p className="fc-account-muted">{t("A new device may also need your separate encryption recovery key. Your identity provider never receives your FreeCollab password.")}</p></section> : !status ? <section className="fc-account-section"><p className="fc-account-muted">{t("Your encryption device could not be loaded.")}</p><button type="button" className="fc-button" onClick={() => void load()}><RefreshCw size={15} />{t("Try again")}</button></section> : <>
          {section === 'recovery' && <>
            <section className="fc-account-section"><div className="fc-account-section-heading"><div><span className="fc-kicker">{t("Keep a way back")}</span><h3>{t("Encrypted history recovery")}</h3></div><span className="fc-badge">{status.ready ? status.unlocked ? t("Unlocked") : t("Locked") : t("Not set up")}</span></div><p className="fc-account-muted">{t("A recovery key lets a new trusted device recover your encrypted message history. Store it separately from your account password.")}</p>
              {!status.ready && !generatedKey && <button className="fc-button fc-button-primary" type="button" disabled={!!busy} onClick={() => void setup()}>{busy === 'setup' ? <Loader2 size={16} className="fc-account-spinner" /> : <KeyRound size={16} />}{t("Set up encrypted history recovery")}</button>}
              {generatedKey && <div className="fc-secure-generated-key"><span className="fc-kicker">{t("Shown once")}</span><h3>{t("Save your encryption recovery key")}</h3><p>{t("Anyone with this key may recover your encrypted history. Keep it somewhere only you can access.")}</p><code>{generatedKey}</code><div className="fc-account-actions"><button className="fc-button" type="button" onClick={() => void copy(generatedKey, 'Encryption recovery key copied. Store it somewhere private.')}><Copy size={15} />{t("Copy key")}</button><button className="fc-button" type="button" onClick={downloadKey}><Download size={15} />{t("Download key")}</button></div><label className="fc-account-checkbox"><input type="checkbox" checked={keyStored} onChange={event => setKeyStored(event.target.checked)} />{t("I have stored this recovery key somewhere safe.")}</label>{keyStored && <button className="fc-button" type="button" onClick={() => { setGeneratedKey(''); setNotice(t("Your recovery key has been saved.")); }}><Check size={15} />{t("I have saved my key")}</button>}</div>}
              {status.ready && !status.unlocked && <form className="fc-account-form" onSubmit={unlock}><label htmlFor="encrypted-history-recovery-key">{t("Encryption recovery key")}</label><input className="fc-input" id="encrypted-history-recovery-key" type="password" autoComplete="off" spellCheck={false} value={recoveryKey} onChange={event => setRecoveryKey(event.target.value)} disabled={!!busy} required /><button className="fc-button fc-button-primary" type="submit" disabled={!!busy}>{busy === 'unlock' ? <Loader2 size={16} className="fc-account-spinner" /> : <KeyRound size={16} />}{t("Unlock encrypted history")}</button></form>}
              {status.unlocked && !generatedKey && <><p className="fc-account-muted">{t("Your recovery key is currently held in memory on this device. Signing out clears it.")}</p><button className="fc-button" type="button" disabled={!!busy} onClick={() => void run('lock', async () => { const secure = await adapter(); if (!current()) return; secure.stop(); setStatus(previous => previous ? { ...previous, unlocked: false } : previous); setNotice(t("Recovery key cleared from memory. Your local encryption device still holds its existing message keys.")); })}><Lock size={15} />{t("Lock recovery key")}</button></>}
            </section><section className="fc-account-section fc-account-key-note"><KeyRound size={21} /><div><h3>{t("Two different ways to recover")}</h3><p>{t("Your password and sign-in recovery codes restore account access. This encryption recovery key unlocks message history. Resetting your account password cannot replace this key.")}</p></div></section>
          </>}
          {section === 'devices' && <>
            {workspaceScoped && !managedAccount && <p className="fc-account-muted">{t("Sign in with your FreeCollab password to revoke devices across your personal account.")}</p>}{managedAccount && <p className="fc-account-muted">{t("Your encryption identity belongs to this organization. Removing a device requires an organization sign-in from the last five minutes.")}</p>}
            <section className="fc-account-section"><span className="fc-kicker">{t("This browser")}</span><h3>{t("Your device fingerprint")}</h3><p className="fc-account-muted">{t("Compare this fingerprint through a separate trusted channel when another person verifies your device.")}</p><div className="fc-secure-fingerprint"><code>{formatFingerprint(status.fingerprint)}</code><button className="fc-icon-button" type="button" aria-label={t("Copy this device fingerprint")} onClick={() => void copy(status.fingerprint, 'Device fingerprint copied.')}><Copy size={16} /></button></div><p className="fc-secure-device-id">{t("Device ID: ")}{status.device_id}</p></section>
            <section className="fc-account-section"><div className="fc-account-section-heading"><h3>{channelId ? t("Devices in this channel") : t("Your encryption devices")}</h3><button type="button" className="fc-icon-button" disabled={!!busy} aria-label={t("Refresh encryption devices")} onClick={() => void load()}><RefreshCw size={16} /></button></div><p className="fc-account-muted">{t("Verify each device by comparing its fingerprint in person or through another trusted channel. Account sessions and encryption devices have separate trust.")}</p><ul className="fc-secure-device-list">{devices.map(device => {
              const own = device.user_id === identity.current;
              const thisDevice = own && device.device_id === status.device_id;
              return <li key={`${device.matrix_user_id}:${device.device_id}`}><Laptop size={20} /><div><strong>{thisDevice ? t("This browser") : device.name || t("Encryption device")}<span className={`fc-badge ${device.verified ? 'fc-account-success' : ''}`}>{thisDevice ? t("This device") : device.verified ? t("Verified") : t("Unverified")}</span></strong><span>{own ? t("Your account") : t("Account {value0}", { value0: String(device.user_id) })}</span><small>{t("Device ")}{device.device_id}</small>{!thisDevice && !device.verified && <button className="fc-account-link" type="button" disabled={!!busy} onClick={() => { chooseRevocation(null); setVerifying(device); }}>{t("Compare and verify device")}</button>}{own && (!workspaceScoped || managedAccount) && <button className="fc-account-link fc-account-danger" type="button" disabled={!!busy} onClick={() => chooseRevocation(device)} aria-label={t("Revoke {value0}", { value0: String(thisDevice ? 'this encryption device' : `encryption device ${device.device_id}`) })}><Trash2 size={14} />{thisDevice ? t("Revoke this device") : t("Revoke lost device")}</button>}</div></li>;
            })}{!devices.length && <li className="fc-account-muted">{t("No encryption devices are visible yet.")}</li>}</ul></section>
            {verifying && <form className="fc-account-reauth" onSubmit={verify}><h3>{t("Compare this device")}</h3><p>{t("Ask the device owner to open their encryption settings and read their fingerprint to you through a separate trusted channel.")}</p><p className="fc-secure-device-id">{t("Device ")}{verifying.device_id} · {verifying.user_id === identity.current ? t("Your account") : t("Account {value0}", { value0: String(verifying.user_id) })}</p><label htmlFor="secure-verify-fingerprint">{t("Fingerprint from the other device")}</label><input ref={fingerprintInput} className="fc-input" id="secure-verify-fingerprint" type="text" autoComplete="off" spellCheck={false} value={fingerprint} onChange={event => setFingerprint(event.target.value)} disabled={!!busy} required /><label className="fc-account-checkbox"><input type="checkbox" checked={compared} onChange={event => setCompared(event.target.checked)} disabled={!!busy} />{t("I checked this fingerprint through a separate trusted channel.")}</label><div className="fc-account-actions"><button className="fc-button" type="button" disabled={!!busy} onClick={() => { setVerifying(null); setFingerprint(''); }}>{t("Cancel")}</button><button className="fc-button fc-button-primary" type="submit" disabled={!!busy || !compared || !fingerprint.trim()}>{busy === 'verify' ? <Loader2 size={16} className="fc-account-spinner" /> : <ShieldCheck size={16} />}{t("Verify device")}</button></div></form>}
            {revoking && (!workspaceScoped || managedAccount) && <form className="fc-account-reauth fc-secure-revoke" onSubmit={revokeDevice} aria-label={t("Revoke encryption device")} aria-busy={busy === 'revoke'}><h3>{revoking.device_id === status.device_id ? t("Revoke this encryption device?") : t("Revoke this lost device?")}</h3><p>{t("This signs out the device’s linked FreeCollab session and stops it receiving new encrypted message keys. Messages and keys already stored on that device cannot be taken back.")}</p>{revoking.device_id === status.device_id && <p className="fc-account-danger">{t("You are revoking this browser. You will be signed out.")}</p>}<p className="fc-secure-device-id">{t("Device ")}{revoking.device_id}</p>{!managedAccount && <><label htmlFor="secure-revoke-password">{t("Confirm with your password")}</label><input ref={revokePasswordInput} className="fc-input" id="secure-revoke-password" type="password" autoComplete="current-password" value={revokePassword} onChange={event => setRevokePassword(event.target.value)} disabled={!!busy} required />{mfaEnabled && <><label htmlFor="secure-revoke-code">{t("Authentication or recovery code")}</label><input className="fc-input" id="secure-revoke-code" type="text" autoComplete="one-time-code" value={revokeCode} onChange={event => setRevokeCode(event.target.value)} disabled={!!busy} required /></>}</>}<label className="fc-account-checkbox"><input type="checkbox" checked={revokeConfirmed} onChange={event => setRevokeConfirmed(event.target.checked)} disabled={!!busy} />{t("I understand this device will lose access to new encrypted messages.")}</label><div className="fc-account-actions"><button type="button" className="fc-button" disabled={!!busy} onClick={() => chooseRevocation(null)}>{t("Keep device")}</button><button type="submit" className="fc-button" disabled={!!busy || !revokeConfirmed || !managedAccount && (!revokePassword || mfaEnabled && !revokeCode.trim())}>{busy === 'revoke' ? <Loader2 size={16} className="fc-account-spinner" /> : <Trash2 size={16} />}{revoking.device_id === status.device_id ? t("Revoke device and sign out") : t("Revoke device")}</button></div></form>}
          </>}
        </>}
      </div><footer className="fc-account-footer"><ShieldCheck size={15} /><span>{t("Verify trust before sharing encrypted content.")}</span><button className="fc-button" type="button" disabled={busy === 'revoke'} onClick={close}>{t("Done")}</button></footer>
    </section>
  </div>, document.body);
}
