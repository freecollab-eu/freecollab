import { api } from './api';
import { io, Socket } from 'socket.io-client';

const API_URL = import.meta.env.VITE_API_URL || undefined;

// Singleton socket instance
let socket: Socket | null = null;

export function getSocket(): Socket {
  if (!socket) {
    socket = io(API_URL, {
      auth: (callback) => callback({ token: api.getAccessToken() }),
      transports: ['websocket', 'polling'],
    });

    // Auto-reconnect with updated token
    socket.on('connect_error', (error) => {
      console.error('Socket connection error:', error.message);
    });

    socket.on('connect', () => {
      console.log('Socket connected:', socket?.id);
    });

    socket.on('disconnect', (reason) => {
      console.log('Socket disconnected:', reason);
    });
  }
  return socket;
}

export function disconnectSocket() {
  if (socket) {
    socket.disconnect();
    socket = null;
  }
}

export function reconnectSocket() {
  // Keep the singleton and its listeners when refreshing authentication.
  const current = getSocket();
  current.disconnect();
  current.connect();
  return current;
}

// Presence helpers
export function setPresenceStatus(status: 'online' | 'away' | 'busy' | 'offline', customStatus?: string) {
  const s = getSocket();
  s.emit('set_status', { status, custom_status: customStatus });
}

// Typing indicator helpers
export function sendTypingStart(channelId: string) {
  const s = getSocket();
  s.emit('typing', { channel_id: channelId });
}

export function sendTypingStop(channelId: string) {
  const s = getSocket();
  s.emit('stop_typing', { channel_id: channelId });
}

// Join/leave channels
export function joinChannel(channelId: string) {
  const s = getSocket();
  s.emit('join', { channel_id: channelId });
}

export function leaveChannel(channelId: string) {
  const s = getSocket();
  s.emit('leave', { channel_id: channelId });
}

// Export default socket getter
export { socket };
export default getSocket;
