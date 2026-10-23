import { useEffect, useRef, useState } from 'react';
import { Camera, Loader2 } from 'lucide-react';
import { Avatar } from '../components/Avatar';
import { api, type User } from '../lib/api';
import { announceAvatarChange, avatarApi, prepareProfilePhoto } from '../lib/avatar-api';
import { t, useI18n } from '../i18n';

export function AccountPhoto({ user }: { user: User }) {
  useI18n();
  const [hasPhoto, setHasPhoto] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [demo, setDemo] = useState(document.getElementById('root')?.hasAttribute('data-demo-application') || false);
  const [ready, setReady] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const generation = useRef(0);
  const token = useRef(api.getAccessToken());
  useEffect(() => {
    const request = ++generation.current;
    const controller = new AbortController();
    const invalidate = () => { ++generation.current; controller.abort(); setReady(false); setError(''); setNotice(''); setBusy(false); };
    void api.request<{ demo_instance?: boolean }>('/capabilities', { signal: controller.signal }).then(value => {
      if (request !== generation.current || token.current !== api.getAccessToken()) return;
      setDemo(!!value.demo_instance); setReady(true);
    }).catch(() => { if (!controller.signal.aborted) setError(t('Photo settings could not be loaded. Reopen your account to try again.')); });
    window.addEventListener('freecollab:auth-changed', invalidate);
    return () => { invalidate(); window.removeEventListener('freecollab:auth-changed', invalidate); };
  }, []);
  const current = (request: number) => request === generation.current && token.current === api.getAccessToken() && user.id === api.getCurrentUserId();
  async function change(file?: File) {
    if (!ready || demo || busy) return;
    const request = generation.current;
    setBusy(true); setError(''); setNotice('');
    try {
      const photo = file ? await prepareProfilePhoto(file) : null;
      if (!current(request)) return;
      if (photo) await avatarApi.upload(photo); else await avatarApi.remove();
      if (!current(request)) return;
      announceAvatarChange(user.id);
      setNotice(t(photo ? 'Profile photo updated.' : 'Profile photo removed.'));
    } catch (failure) { if (current(request)) setError(t(failure instanceof Error ? failure.message : 'Your profile photo could not be saved.')); }
    finally { if (current(request)) setBusy(false); if (input.current) input.current.value = ''; }
  }
  return <section className="fc-account-section fc-account-photo"><Avatar userId={user.id} name={user.name} size={72} onPhotoState={setHasPhoto} />
    <div><h3>{t('Profile photo')}</h3><p className="fc-account-muted">{t('Your photo is visible to people who currently share a workspace or channel with you.')}</p>
      {demo ? <p className="fc-account-muted">{t('These are sample profile photos. Upload your own photo in your FreeCollab installation; keep personal photos out of the public demo.')}</p> : <>
        <p className="fc-account-muted">{t('PNG, JPEG or WebP, up to 5 MB. We crop a square and remove image metadata.')}</p>
        <input ref={input} className="fc-visually-hidden" id="account-profile-photo" aria-label={t('Profile photo')} type="file" accept="image/png,image/jpeg,image/webp" disabled={!ready || busy} onChange={event => { const file = event.target.files?.[0]; if (file) void change(file); }} />
        <div className="fc-account-actions"><button type="button" className="fc-button" disabled={!ready || busy} onClick={() => input.current?.click()}>{busy ? <Loader2 size={15} className="fc-account-spinner" /> : <Camera size={15} />}{t(hasPhoto ? 'Change photo' : 'Upload photo')}</button>
          {hasPhoto && <button type="button" className="fc-account-link" disabled={!ready || busy} onClick={() => void change()}>{t('Use initials instead')}</button>}</div>
      </>}
      {error && <p className="fc-account-error" role="alert">{error}</p>}{notice && <p className="fc-account-success" role="status">{notice}</p>}
    </div></section>;
}
