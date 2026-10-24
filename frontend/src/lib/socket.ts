import { api } from './api';
import { io, Socket } from 'socket.io-client';

const API_URL = import.meta.env.VITE_API_URL || undefined;
// REST prefixes such as /api are not Socket.IO namespaces. The realtime
// service uses its default namespace and /socket.io path on the API origin.
const SOCKET_ORIGIN = API_URL ? new URL(API_URL, window.location.href).origin : undefined;

// Singleton socket instance
let socket: Socket | null = null;
let socketCredential: string | null = null;

export function getSocket(): Socket {
  if (!socket) {
    socket = io(SOCKET_ORIGIN, {
      auth: (callback) => { socketCredential = api.getAccessToken(); callback({ token: socketCredential }); },
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
      socketCredential = null;
      console.log('Socket disconnected:', reason);
    });
  }
  return socket;
}

export function disconnectSocket() {
  if (socket) {
    socket.disconnect();
    socket = null;
    socketCredential = null;
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
export async function setPresenceStatus(status: 'online' | 'away' | 'busy' | 'offline', customStatus?: string | null) {
  const token = api.getAccessToken();
  if (!token) throw new Error('Sign in again before changing your status.');
  const s = getSocket();
  // Do not buffer profile text across a disconnect or another account's next
  // handshake. A retry must be an explicit action in the current sign-in.
  if (!s.connected || socketCredential !== token) throw new Error('Your status could not be updated. Please try again.');
  const result = await s.volatile.timeout(10000).emitWithAck('set_status', { status, ...(customStatus !== undefined ? { custom_status: customStatus } : {}) });
  if (token !== api.getAccessToken()) throw new Error('The signed-in account changed.');
  if (result?.ok !== true) throw new Error('Your status could not be updated. Please try again.');
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
