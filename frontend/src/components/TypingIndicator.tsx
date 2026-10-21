import { useI18n } from '../i18n';
import { useState, useEffect } from 'react';
import { getSocket } from '../lib/socket';
import '../styles/panels.css';

interface TypingIndicatorProps {
  channelId: string;
  currentUserId: string;
}

interface TypingUser {
  user_id: string;
  user_name: string;
}

export function TypingIndicator({ channelId, currentUserId }: TypingIndicatorProps) {
  useI18n();
  const [typingUsers, setTypingUsers] = useState<TypingUser[]>([]);

  useEffect(() => {
    const socket = getSocket();

    const handleTyping = (data: { channel_id: string; user_id: string; user_name: string }) => {
      if (data.channel_id === channelId && data.user_id !== currentUserId) {
        setTypingUsers(prev => {
          if (prev.some(u => u.user_id === data.user_id)) return prev;
          return [...prev, { user_id: data.user_id, user_name: data.user_name }];
        });
      }
    };

    const handleStopTyping = (data: { channel_id: string; user_id: string }) => {
      if (data.channel_id === channelId) {
        setTypingUsers(prev => prev.filter(u => u.user_id !== data.user_id));
      }
    };

    socket.on('typing', handleTyping);
    socket.on('stop_typing', handleStopTyping);

    return () => {
      socket.off('typing', handleTyping);
      socket.off('stop_typing', handleStopTyping);
    };
  }, [channelId, currentUserId]);

  if (typingUsers.length === 0) return null;

  const getTypingText = () => {
    if (typingUsers.length === 1) {
      return `${typingUsers[0].user_name} is typing...`;
    } else if (typingUsers.length === 2) {
      return `${typingUsers[0].user_name} and ${typingUsers[1].user_name} are typing...`;
    } else {
      return `${typingUsers[0].user_name} and ${typingUsers.length - 1} others are typing...`;
    }
  };

  return (
    <div className="fc-typing-indicator flex items-center gap-2 px-4 py-1 text-sm">
      <div className="flex gap-1">
        <span className="fc-typing-dot w-1.5 h-1.5 rounded-full animate-bounce" style={{ animationDelay: '0ms' }}></span>
        <span className="fc-typing-dot w-1.5 h-1.5 rounded-full animate-bounce" style={{ animationDelay: '150ms' }}></span>
        <span className="fc-typing-dot w-1.5 h-1.5 rounded-full animate-bounce" style={{ animationDelay: '300ms' }}></span>
      </div>
      <span>{getTypingText()}</span>
    </div>
  );
}

// Hook for emitting typing events with debounce
export function useTypingEmitter(channelId: string) {
  const [isTyping, setIsTyping] = useState(false);
  const socket = getSocket();

  useEffect(() => {
    let timeout: ReturnType<typeof setTimeout>;

    if (isTyping) {
      socket.emit('typing', { channel_id: channelId });
      
      // Auto-stop after 3 seconds of no keystrokes
      timeout = setTimeout(() => {
        socket.emit('stop_typing', { channel_id: channelId });
        setIsTyping(false);
      }, 3000);
    }

    return () => {
      clearTimeout(timeout);
    };
  }, [isTyping, channelId, socket]);

  const startTyping = () => {
    if (!isTyping) {
      setIsTyping(true);
    }
  };

  const stopTyping = () => {
    if (isTyping) {
      socket.emit('stop_typing', { channel_id: channelId });
      setIsTyping(false);
    }
  };

  return { startTyping, stopTyping };
}

export default TypingIndicator;
