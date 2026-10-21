import { t, useI18n } from '../i18n';
import { useState, useEffect } from 'react';
import { api, UserPresence } from '../lib/api';
import { getSocket, setPresenceStatus } from '../lib/socket';
import '../styles/panels.css';

interface PresenceIndicatorProps {
  userId: string;
  showLabel?: boolean;
  size?: 'sm' | 'md' | 'lg';
}

export function PresenceIndicator({ userId, showLabel = false, size = 'md' }: PresenceIndicatorProps) {
  useI18n();
  const [presence, setPresence] = useState<UserPresence | null>(null);

  useEffect(() => {
    loadPresence();
    const socket = getSocket();

    const handlePresenceUpdate = (data: { user_id: string; status: string; custom_status?: string }) => {
      if (data.user_id === userId) {
        setPresence(prev => prev ? {
          ...prev,
          status: data.status as UserPresence['status'],
          custom_status: data.custom_status,
        } : null);
      }
    };

    socket.on('presence', handlePresenceUpdate);

    return () => {
      socket.off('presence', handlePresenceUpdate);
    };
  }, [userId]);

  const loadPresence = async () => {
    try {
      const data = await api.getUserPresence(userId);
      setPresence(data);
    } catch (err) {
      // User might not exist or be offline
      setPresence({
        user_id: userId,
        status: 'offline',
        last_seen: new Date().toISOString(),
      });
    }
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'online': return 'fc-presence-online';
      case 'away': return 'fc-presence-away';
      case 'busy': return 'fc-presence-busy';
      default: return 'fc-presence-offline';
    }
  };

  const getStatusLabel = (status: string) => {
    switch (status) {
      case 'online': return 'Online';
      case 'away': return 'Away';
      case 'busy': return 'Do not disturb';
      default: return 'Offline';
    }
  };

  const sizeClasses = {
    sm: 'w-2 h-2',
    md: 'w-3 h-3',
    lg: 'w-4 h-4',
  };

  const status = presence?.status || 'offline';

  return (
    <div className="flex items-center space-x-1">
      <span className={`${sizeClasses[size]} ${getStatusColor(status)} fc-presence-dot rounded-full border-2`}></span>
      {showLabel && (
        <span className="text-xs fc-muted">
          {presence?.custom_status || getStatusLabel(status)}
        </span>
      )}
    </div>
  );
}

// Status selector component
interface PresenceStatusSelectorProps {
  onClose?: () => void;
}

export function PresenceStatusSelector({ onClose }: PresenceStatusSelectorProps) {
  useI18n();
  const [customStatus, setCustomStatus] = useState('');
  const [currentStatus, setCurrentStatus] = useState<'online' | 'away' | 'busy'>('online');

  const statuses: { value: 'online' | 'away' | 'busy'; label: string; icon: string; color: string }[] = [
    { value: 'online', label: t("Online"), icon: '🟢', color: 'fc-presence-online' },
    { value: 'away', label: t("Away"), icon: '🟡', color: 'fc-presence-away' },
    { value: 'busy', label: t("Do not disturb"), icon: '🔴', color: 'fc-presence-busy' },
  ];

  const handleStatusChange = (status: 'online' | 'away' | 'busy') => {
    setCurrentStatus(status);
    setPresenceStatus(status, customStatus || undefined);
    onClose?.();
  };

  const handleCustomStatusSubmit = () => {
    setPresenceStatus(currentStatus, customStatus || undefined);
    onClose?.();
  };

  return (
    <div className="fc-presence-selector">
      <h3 className="font-semibold mb-3">{t("Set your status")}</h3>
      
      <div className="space-y-2 mb-4">
        {statuses.map(({ value, label, icon }) => (
          <button
            key={value}
            onClick={() => handleStatusChange(value)}
            className={`fc-presence-choice ${
              currentStatus === value ? 'is-active' : ''
            }`}
          >
            <span>{icon}</span>
            <span className="fc-text">{label}</span>
          </button>
        ))}
      </div>

      <div className="fc-panel-section">
        <input
          type="text"
          value={customStatus}
          onChange={(e) => setCustomStatus(e.target.value)}
          placeholder={t("Set a custom status...")}
          className="fc-input text-sm"
          maxLength={50}
        />
        <button
          onClick={handleCustomStatusSubmit}
          className="fc-button fc-button-primary w-full mt-2"
        >
          {t("Save Status")}</button>
      </div>
    </div>
  );
}

// Online members list component
interface OnlineMembersListProps {
  channelId?: string;
}

export function OnlineMembersList({ channelId }: OnlineMembersListProps) {
  useI18n();
  const [onlineUsers, setOnlineUsers] = useState<UserPresence[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    loadOnlineUsers();
    const socket = getSocket();

    const handlePresenceUpdate = () => {
      loadOnlineUsers();
    };

    socket.on('presence', handlePresenceUpdate);

    return () => {
      socket.off('presence', handlePresenceUpdate);
    };
  }, [channelId]);

  const loadOnlineUsers = async () => {
    try {
      const data = channelId 
        ? await api.getChannelPresence(channelId)
        : await api.getOnlineUsers();
      setOnlineUsers(data);
    } catch (err) {
      console.error('Failed to load online users:', err);
    } finally {
      setLoading(false);
    }
  };

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'online': return '🟢';
      case 'away': return '🟡';
      case 'busy': return '🔴';
      default: return '⚪';
    }
  };

  if (loading) {
    return (
      <div className="p-4 text-center">
        <div className="fc-panel-spinner mx-auto"></div>
      </div>
    );
  }

  return (
    <div className="p-2">
      <h3 className="text-xs font-semibold fc-muted uppercase tracking-wider px-2 mb-2">
        {t("Online — ")}{onlineUsers.length}
      </h3>
      <div className="space-y-1">
        {onlineUsers.map(user => (
          <div
            key={user.user_id}
            className="fc-presence-member"
          >
            <span>{getStatusIcon(user.status)}</span>
            <span className="text-sm truncate">{user.user_id}</span>
            {user.custom_status && (
              <span className="text-xs fc-muted truncate">— {user.custom_status}</span>
            )}
          </div>
        ))}
        {onlineUsers.length === 0 && (
          <p className="text-sm fc-muted px-2">{t("No users online")}</p>
        )}
      </div>
    </div>
  );
}

export default PresenceIndicator;
