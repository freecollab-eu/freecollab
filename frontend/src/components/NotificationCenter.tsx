import { t, useI18n, currentLocale } from '../i18n';
import { useState, useEffect, useCallback, useRef } from 'react';
import { createPortal } from 'react-dom';
import { Bell, Check, CheckCircle2, Heart, Mail, MessageCircle, Megaphone, Settings2, Smile, X, FileText, Users } from 'lucide-react';
import { api, Notification, NotificationPreference, type Message } from '../lib/api';
import { getSocket } from '../lib/socket';
import { useModalFocus } from '../lib/useModalFocus';
import { formatDistanceToNow } from 'date-fns';
import { sv, enUS } from 'date-fns/locale';
import { getSecureMessaging } from '../lib/secure-client';
import '../styles/panels.css';

interface NotificationCenterProps {
  onClose?: () => void;
  onOpenMessage?: (message: Message) => void;
}

const notificationIcon = (type: string) => {
  switch (type) {
    case 'mention': return Megaphone;
    case 'reply': case 'comment': return MessageCircle;
    case 'reaction': return Smile;
    case 'like': return Heart;
    case 'post': return FileText;
    case 'message': return Mail;
    default: return Bell;
  }
};

export function NotificationCenter({ onClose, onOpenMessage }: NotificationCenterProps) {
  useI18n();
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [showSettings, setShowSettings] = useState(false);
  const [error, setError] = useState('');
  const [marking, setMarking] = useState('');
  const [notice, setNotice] = useState('');
  const dialog = useRef<HTMLElement>(null);
  useModalFocus(dialog, onClose || (() => {}), !!onClose);

  const loadNotifications = useCallback(async () => {
    setError('');
    try {
      const [notifs, countData] = await Promise.all([
        api.getNotifications(50, 0, false),
        api.getUnreadNotificationCount()
      ]);
      setNotifications(notifs);
      setUnreadCount(countData.unread_count);
    } catch {
      setError(t("Notifications could not be loaded. Please try again."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadNotifications();
    const socket = getSocket();
    const handleNewNotification = (notification: Notification) => {
      setNotifications(prev => prev.some(item => item.id === notification.id) ? prev : [{ ...notification, read: notification.read ?? false }, ...prev]);
      void api.getUnreadNotificationCount().then(data => setUnreadCount(data.unread_count)).catch(() => {});
    };
    socket.on('notification', handleNewNotification);
    socket.on('members_changed', loadNotifications);
    return () => { socket.off('notification', handleNewNotification); socket.off('members_changed', loadNotifications); };
  }, [loadNotifications]);

  const markAsRead = async (notificationId: string) => {
    if (marking) return;
    setMarking(notificationId); setError(''); setNotice('');
    try {
      await api.markNotificationRead(notificationId);
      setNotifications(prev => prev.map(n => n.id === notificationId ? { ...n, read: true } : n));
      setUnreadCount(prev => Math.max(0, prev - 1));
      setNotice(t("Marked as read."));
      window.dispatchEvent(new Event('freecollab:notifications-updated'));
      return true;
    } catch { setError(t("This notification could not be marked as read. Please try again.")); }
    finally { setMarking(''); }
    return false;
  };

  const openNotification = async (notification: Notification) => {
    if (marking) return;
    const notificationOwner=api.getAccessToken();
    if (!notification.read && !await markAsRead(notification.id)) return;
    if(notificationOwner!==api.getAccessToken())return;
    if (['activity_feed_post', 'workflow_run', 'encrypted_work'].includes(notification.entity_type || '') && notification.channel_id) {
      const token = api.getAccessToken(); setMarking(notification.id); setError('');
      try {
        const channel = await api.getChannel(notification.channel_id);
        if (token !== api.getAccessToken()) return;
        window.dispatchEvent(new CustomEvent('freecollab:open-workspace-tools', { detail: { channel,
          tab: notification.entity_type === 'activity_feed_post' ? 'feed' : notification.entity_type === 'encrypted_work' ? 'shared-work' : 'workflows',
          ...(notification.entity_type !== 'encrypted_work' ? { resourceId: notification.entity_id || undefined, resourceKind: notification.entity_type === 'workflow_run' ? 'workflow-run' : 'feed' } : {}),
        } })); onClose?.();
      } catch (failure) { if (token === api.getAccessToken()) setError(failure instanceof Error ? failure.message : t('This conversation is unavailable.')); }
      finally { if (token === api.getAccessToken()) setMarking(''); }
      return;
    }
    if (!onOpenMessage || !['message', 'encrypted_message', 'message_reminder'].includes(notification.entity_type || '') || !notification.entity_id) return;
    setMarking(notification.id); setError('');
    try {
      let message: Message;
      let targetId=notification.entity_id, targetChannel:string|null=null, encrypted=notification.entity_type==='encrypted_message';
      if(notification.entity_type==='message_reminder'){
        const target=await api.request<{channel_id:string;item_id:string;kind:string}>('/reminders/'+encodeURIComponent(notification.entity_id)+'/target');
        if(notificationOwner!==api.getAccessToken())return;
        targetId=target.item_id;targetChannel=target.channel_id;encrypted=target.kind==='matrix';
      }
      if (encrypted) {
        const parameters = new URLSearchParams(notification.action_url?.startsWith('#') ? notification.action_url.slice(1) : '');
        const channelId = targetChannel || parameters.get('channel');
        if (!channelId) throw new Error(t("This conversation is unavailable."));
        const client = await getSecureMessaging(), event = await client.getDecryptedEvent(channelId, targetId);
        const { roomId } = await client.roomFor(channelId);
        const content = event.getContent();
        if (event.getRoomId() !== roomId || event.getType() !== 'm.room.message' || event.isRedacted() || typeof content.body !== 'string') throw new Error(t("This message is no longer available."));
        message = api.rememberMessage({ id: targetId, channel_id: channelId, author_id: (event.getSender() || '').replace(/^@fc_([^:]+):.*$/, '$1'), content: content.body, encrypted: true, created_at: new Date(event.getTs()).toISOString(), parent_id: content['m.relates_to']?.rel_type === 'm.thread' ? content['m.relates_to'].event_id : undefined });
      } else message = await api.request<Message>(`/messages/${encodeURIComponent(targetId)}`);
      if(notificationOwner!==api.getAccessToken())return;
      if (message.deleted) throw new Error(t("This message is no longer available."));
      onOpenMessage(message); onClose?.();
    } catch (failure) { if(notificationOwner===api.getAccessToken())setError(failure instanceof Error ? failure.message : t("This conversation is no longer available.")); }
    finally { if(notificationOwner===api.getAccessToken())setMarking(''); }
  };

  const markAllAsRead = async () => {
    if (marking) return;
    setMarking('all'); setError(''); setNotice('');
    try {
      await api.markAllNotificationsRead();
      setNotifications(prev => prev.map(n => ({ ...n, read: true })));
      setUnreadCount(0);
      setNotice(t("You’re all caught up."));
      window.dispatchEvent(new Event('freecollab:notifications-updated'));
    } catch { setError(t("Notifications could not be marked as read. Please try again.")); }
    finally { setMarking(''); }
  };

  const panel = (
    <section ref={dialog} className="fc-notifications" aria-label={t("Notifications")} role={onClose ? 'dialog' : undefined} aria-modal={onClose ? true : undefined} tabIndex={-1}>
      <header className="fc-panel-header">
        <div className="fc-notification-heading"><span className="fc-kicker">{t("Your activity")}</span><div><h2>{t("Notifications")}</h2>{unreadCount > 0 && <span className="fc-badge fc-badge-accent">{unreadCount} {t(" unread")}</span>}</div></div>
        <div className="flex items-center gap-1">
          <button type="button" onClick={() => setShowSettings(!showSettings)} className="fc-icon-button" aria-label={t("Notification settings")} aria-pressed={showSettings} title={t("Settings")}><Settings2 size={17} /></button>
          {onClose && <button data-autofocus type="button" onClick={onClose} className="fc-icon-button" aria-label={t("Close notifications")} title={t("Close · Esc")}><X size={18} /></button>}
        </div>
      </header>
      {error && <div role="alert" className="fc-alert fc-panel-error m-4">{error}<button className="fc-text-button block mt-2" type="button" onClick={() => void loadNotifications()}>{t("Try again")}</button></div>}
      {notice && !showSettings && <p className="fc-notification-feedback" role="status"><CheckCircle2 size={14} />{notice}</p>}
      {loading ? <div className="fc-notification-empty" role="status"><span className="fc-panel-spinner" />{t("Loading notifications…")}</div> : showSettings ? <NotificationSettings onClose={() => setShowSettings(false)} /> : <>
        {unreadCount > 0 && <div className="fc-notification-toolbar"><button type="button" onClick={() => void markAllAsRead()} disabled={!!marking} className="fc-text-button"><Check size={14} />{marking === 'all' ? t("Marking…") : t("Mark all read")}</button></div>}
        <div className="fc-notification-list">
          {notifications.length === 0 ? <div className="fc-notification-empty"><span className="fc-notification-empty-icon"><Bell size={26} strokeWidth={1.3} aria-hidden="true" /></span><p>{t("No notifications yet")}</p><span>{t("Nothing needs your attention here. Back to the conversation when you’re ready.")}</span></div> : notifications.map(notification => {
            const Icon = notificationIcon(notification.type);
            return <button type="button" key={notification.id} disabled={!!marking} onClick={() => void openNotification(notification)} className={`fc-notification-item ${!notification.read ? 'is-unread' : ''}`} aria-label={t("{value0}{value1}", { value0: String(notification.title), value1: String(notification.read ? '' : ', unread. Mark as read') })}>
              <span className="fc-notification-icon"><Icon size={18} strokeWidth={1.6} /></span>
              <span className="fc-notification-copy"><strong>{notification.title}</strong><span>{notification.body}</span><small>{formatDistanceToNow(new Date(notification.created_at), { addSuffix: true, locale: currentLocale() === 'sv' ? sv : enUS })}</small></span>
              {!notification.read && <span className="fc-unread-dot" aria-hidden="true" />}
            </button>;
          })}
        </div>
      </>}
    </section>
  );
  return onClose ? createPortal(<div className="fc-notification-layer" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>{panel}</div>, document.body) : panel;
}

interface NotificationSettingsProps { onClose: () => void; }

function NotificationSettings({ onClose }: NotificationSettingsProps) {
  useI18n();
  const [preferences, setPreferences] = useState<NotificationPreference | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    void (async () => {
      try { setPreferences(await api.getNotificationPreferences()); }
      catch { setError(t("Failed to load settings")); }
      finally { setLoading(false); }
    })();
  }, []);

  const updatePreference = async (key: keyof NotificationPreference, value: boolean) => {
    if (!preferences) return;
    setSaving(true); setSaved(false); setError('');
    setPreferences({ ...preferences, [key]: value });
    try { await api.updateNotificationPreferences({ [key]: value }); setSaved(true); }
    catch { setPreferences(preferences); setError(t("Your preference could not be saved. Please try again.")); }
    finally { setSaving(false); }
  };

  if (loading) return <div role="status" className="fc-notification-empty"><span className="fc-panel-spinner" />{t("Loading settings…")}</div>;
  if (!preferences) return <p role="alert" className="fc-panel-error p-4">{error}</p>;

  const toggles: { key: keyof NotificationPreference; label: string; icon: typeof Bell }[] = [
    { key: 'mentions', label: t("Mentions"), icon: Megaphone },
    { key: 'replies', label: t("Replies"), icon: MessageCircle },
    { key: 'likes', label: t("Likes"), icon: Heart },
    { key: 'comments', label: t("Comments"), icon: MessageCircle },
    { key: 'follows', label: t("Follows"), icon: Users },
    { key: 'system', label: t("System updates"), icon: Bell },
    { key: 'email', label: t("Email summaries"), icon: Mail },
  ];

  return <div className="fc-notification-settings">
    <div className="flex items-center justify-between gap-2 mb-4"><h3>{t("Notification Settings")}</h3><button type="button" onClick={onClose} className="fc-text-button">{t("Done")}</button></div>
    {error && <p className="fc-panel-error text-sm mb-3" role="alert">{error}</p>}
    <p className="fc-notification-settings-description">{t("Choose which activity appears in your notifications.")}</p>
    {toggles.map(({ key, label, icon: Icon }) => <label key={key} className="fc-notification-toggle">
      <span><Icon size={17} strokeWidth={1.6} aria-hidden="true" />{label}</span>
      <input type="checkbox" checked={preferences[key] as boolean} onChange={event => void updatePreference(key, event.target.checked)} disabled={saving} />
    </label>)}
    <p className={`fc-notification-save-status ${saved ? 'is-saved' : ''}`} role="status">{saving ? t("Saving…") : saved ? <><CheckCircle2 size={13} />{t("Preferences saved")}</> : t("Changes save automatically.")}</p>
  </div>;
}

interface NotificationBellProps { onClick: () => void; }

export function NotificationBell({ onClick }: NotificationBellProps) {
  useI18n();
  const [unreadCount, setUnreadCount] = useState(0);
  useEffect(() => {
    void api.getUnreadNotificationCount().then(data => setUnreadCount(data.unread_count)).catch(() => {});
    const socket = getSocket();
    const refreshCount = () => { void api.getUnreadNotificationCount().then(data => setUnreadCount(data.unread_count)).catch(() => {}); };
    const handleNewNotification = refreshCount;
    window.addEventListener('freecollab:notifications-updated', refreshCount);
    socket.on('notification', handleNewNotification);
    socket.on('members_changed', refreshCount);
    return () => { socket.off('notification', handleNewNotification); socket.off('members_changed', refreshCount); window.removeEventListener('freecollab:notifications-updated', refreshCount); };
  }, []);
  return <button type="button" onClick={onClick} className="fc-icon-button fc-notification-bell" aria-label={t("Notifications")} aria-haspopup="dialog" title={t("Notifications")}><Bell size={18} strokeWidth={1.7} aria-hidden="true" />{unreadCount > 0 && <span className="fc-notification-count">{unreadCount > 99 ? '99+' : unreadCount}<span className="sr-only"> {t(" unread")}</span></span>}</button>;
}

export default NotificationCenter;
