// FreeCollab API client. Secure channels use the client-side Matrix adapter.
import type { MessageReference } from './message-references';

const getSecureMessaging = async () => (await import('./secure-client')).getSecureMessaging();

const API_URL = import.meta.env.VITE_API_URL || '/api';

interface Token {
  access_token: string;
  token_type: string;
  user_id: string;
  public_key: string;
  encrypted_private_key: string | null;
}

interface MFAChallenge {
  mfa_required: true;
  challenge_token: string;
  expires_in: number;
}

interface User {
  id: string;
  name: string;
  email: string;
  public_key: string | null;
  session_scope?: 'account' | 'workspace';
  account_type?: 'personal' | 'managed';
  workspace_id?: string | null;
  role?: string;
}

interface Channel {
  id: string;
  name: string | null;
  type: string;
  encrypted_channel_key: string | null;
  role?: 'admin' | 'member';
  workspace_id?: string;
  secure?: boolean;
  matrix_room_id?: string;
  description?: string;
  metadata_version?: number;
}

interface ChannelInvite {
  id: string;
  channel_id: string;
  expires_at: string;
  token?: string;
  revoked_at?: string | null;
  used_at?: string | null;
  accepted_at?: string | null;
}

export class APIError extends Error {
  constructor(message: string, public status: number, public code?: string) { super(message); }
}

async function responseError(response: Response, fallback: string): Promise<APIError> {
  const data = await response.json().catch(() => null);
  const detail = data?.detail;
  const message = typeof detail === 'string' ? detail : Array.isArray(detail)
    ? detail.map((item: { msg?: string }) => item.msg || 'Invalid input').join('. ')
    : typeof detail?.message === 'string' ? detail.message : fallback;
  return new APIError(message, response.status, typeof detail?.code === 'string' ? detail.code : undefined);
}

interface Message {
  id: string;
  channel_id: string;
  author_id: string;
  content: string;
  encrypted: boolean;
  created_at: string;
  edited_at?: string;
  deleted?: boolean;
  parent_id?: string;
  thread_count?: number;
  mentions?: string[];
  references?: MessageReference[];
  locked?: boolean;
}

interface Reaction {
  emoji: string;
  count: number;
  users: string[];
  user_reacted: boolean;
}

interface ReadReceipt {
  user_id: string;
  last_read_message_id: string;
  read_at: string;
}

interface ThreadReply {
  parent_id: string;
  reply: Message;
  thread_count: number;
}

interface MentionableMember {
  id: string;
  username: string;
}

// Presence
interface UserPresence {
  user_id: string;
  status: 'online' | 'away' | 'busy' | 'offline';
  last_seen: string;
  custom_status?: string;
}

// Posts/Flows
interface Post {
  id: string;
  content: string;
  flow_id: string;
  author_id: string;
  author_name?: string;
  like_count: number;
  comment_count: number;
  created_at: string;
  updated_at?: string;
}

interface PostLike {
  id: string;
  post_id: string;
  user_id: string;
  created_at: string;
}

// Comments
interface Comment {
  id: string;
  content: string;
  entity_type: string;
  entity_id: string;
  author_id: string;
  author_name?: string;
  parent_id?: string;
  like_count: number;
  reply_count: number;
  created_at: string;
  updated_at?: string;
}

// Notifications
interface Notification {
  channel_id?: string | null;
  id: string;
  type: string;
  title: string;
  body: string | null;
  actor_id?: string | null;
  entity_type?: string | null;
  entity_id?: string | null;
  action_url?: string | null;
  read: boolean;
  created_at: string;
}

interface NotificationPreference {
  mentions: boolean;
  likes: boolean;
  comments: boolean;
  replies: boolean;
  follows: boolean;
  system: boolean;
  in_app: boolean;
  email: boolean;
  push: boolean;
}

interface ChannelMember {
  user_id: string;
  name: string;
  public_key: string | null;
  role: string;
}

class FreeCollabAPI {
  private messageChannels = new Map<string, string>();

  rememberMessage(message: Message): Message {
    this.messageChannels.set(message.id, message.channel_id);
    return message;
  }

  private async secureChannel(channelId: string) {
    const secure = await getSecureMessaging();
    return await secure.handles(channelId) ? secure : null;
  }

  private async secureMessage(messageId: string) {
    if (!messageId.startsWith("$")) return null;
    const channelId = this.messageChannels.get(messageId);
    if (!channelId) throw new Error("Open this encrypted conversation before changing a message.");
    const adapter = await this.secureChannel(channelId);
    if (!adapter) throw new Error("This encrypted conversation is unavailable.");
    return { adapter, channelId };
  }

  // Embedded clients and the real product demo never adopt a stored account.
  // Decide before the constructor reads storage, including on a demo reload.
  private memoryOnly = typeof document !== 'undefined' && ['data-embed-application', 'data-demo-application'].some(attribute => document.getElementById('root')?.hasAttribute(attribute));
  private token: string | null = null;
  private userId: string | null = null;
  private publicKey: string | null = null;
  private encryptedPrivateKey: string | null = null;

  constructor() {
    this.syncStoredSession();
  }

  isMemorySession() { return this.memoryOnly; }

  useMemorySession() {
    this.memoryOnly = true;
    this.logout();
  }

  syncStoredSession() {
    if (this.memoryOnly) return;
    this.token = localStorage.getItem('freecollab_token');
    this.userId = localStorage.getItem('freecollab_user_id');
    this.publicKey = localStorage.getItem('freecollab_public_key');
    this.encryptedPrivateKey = localStorage.getItem('freecollab_encrypted_private_key');
  }

  async request<T>(
    endpoint: string,
    options: RequestInit = {}
  ): Promise<T> {
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      ...options.headers as Record<string, string>,
    };

    const requestToken = this.token;
    if (requestToken) {
      headers['Authorization'] = `Bearer ${requestToken}`;
    }

    const response = await fetch(`${API_URL}${endpoint}`, {
      ...options,
      headers,
    });

    if (!response.ok) {
      if (response.status === 401 && requestToken && requestToken === this.token && !['/auth/login', '/auth/register'].includes(endpoint)) {
        window.dispatchEvent(new Event('freecollab:session-expired'));
      }
      throw await responseError(response, 'Request failed. Please try again.');
    }
    if (response.status === 204) return undefined as T;
    const data = await response.json();
    if (requestToken === this.token && Array.isArray(data?.policy_warnings) && data.policy_warnings.length)
      window.dispatchEvent(new CustomEvent('freecollab:policy-warning', {detail:{count:data.policy_warnings.length}}));
    return data;
  }

  private async rawRequest(endpoint: string, options: RequestInit = {}): Promise<Response> {
    const requestToken = this.token;
    const headers = new Headers(options.headers);
    if (requestToken) headers.set('Authorization', `Bearer ${requestToken}`);
    const response = await fetch(`${API_URL}${endpoint}`, { ...options, headers });
    if (!response.ok) {
      if (response.status === 401 && requestToken && requestToken === this.token) {
        window.dispatchEvent(new Event('freecollab:session-expired'));
      }
      throw await responseError(response, 'Request failed. Please try again.');
    }
    return response;
  }

  async uploadMultipart<T>(endpoint: string, body: FormData): Promise<T> {
    return (await this.rawRequest(endpoint, { method: 'POST', body })).json();
  }

  async requestBlob(endpoint: string, options: RequestInit = {}): Promise<Blob> {
    return (await this.rawRequest(endpoint, options)).blob();
  }

  // Auth
  async register(name: string, email: string, password: string): Promise<Token> {
    const data = await this.request<Token>('/auth/register', {
      method: 'POST',
      body: JSON.stringify({ name, email, password }),
    });
    this.setAuth(data);
    return data;
  }

  async login(email: string, password: string): Promise<Token | MFAChallenge> {
    const data = await this.request<Token | MFAChallenge>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    });
    if ('access_token' in data) this.setAuth(data);
    return data;
  }

  private setAuth(data: Token) {
    this.messageChannels.clear();
    this.token = data.access_token;
    this.userId = data.user_id;
    this.publicKey = data.public_key;
    this.encryptedPrivateKey = data.encrypted_private_key;
    
    if (!this.memoryOnly) {
      localStorage.setItem('freecollab_token', data.access_token);
      localStorage.setItem('freecollab_user_id', data.user_id);
      localStorage.setItem('freecollab_public_key', data.public_key);
      if (data.encrypted_private_key) {
        localStorage.setItem('freecollab_encrypted_private_key', data.encrypted_private_key);
      } else {
        localStorage.removeItem('freecollab_encrypted_private_key');
      }
    }
    window.dispatchEvent(new Event('freecollab:auth-changed'));
  }

  applySession(data: Token) {
    this.setAuth(data);
  }

  getAccessToken(): string | null {
    return this.token;
  }

  getBaseUrl(): string {
    return API_URL;
  }

  logout() {
    this.messageChannels.clear();
    this.token = null;
    this.userId = null;
    this.publicKey = null;
    this.encryptedPrivateKey = null;
    if (!this.memoryOnly) {
      localStorage.removeItem('freecollab_token');
      localStorage.removeItem('freecollab_user_id');
      localStorage.removeItem('freecollab_public_key');
      localStorage.removeItem('freecollab_encrypted_private_key');
    }
    window.dispatchEvent(new Event('freecollab:auth-changed'));
  }

  async logoutSession(): Promise<void> {
    const requestToken = this.token;
    try { await this.request('/auth/logout', { method: 'POST' }); }
    finally { if (requestToken === this.token) this.logout(); }
  }

  isAuthenticated(): boolean {
    return !!this.token;
  }

  getCurrentUserId(): string | null {
    return this.userId;
  }

  getPublicKey(): string | null {
    return this.publicKey;
  }

  getEncryptedPrivateKey(): string | null {
    return this.encryptedPrivateKey;
  }

  async getMe(): Promise<User> {
    return this.request<User>('/auth/me');
  }

  // Channels
  async createChannel(name: string, type: string, memberIds: string[], workspaceId?: string): Promise<Channel> {
    return this.request<Channel>('/channels', {
      method: 'POST',
      body: JSON.stringify({ name, type, member_ids: memberIds, ...(workspaceId ? { workspace_id: workspaceId } : {}) }),
    });
  }

  async getChannels(workspaceId?: string): Promise<Channel[]> {
    return this.request<Channel[]>('/channels' + (workspaceId ? `?workspace_id=${encodeURIComponent(workspaceId)}` : ''));
  }

  async getChannel(channelId: string): Promise<Channel> {
    return this.request<Channel>(`/channels/${channelId}`);
  }

  async getChannelMetadata(channelId: string): Promise<import('./channel-metadata').ChannelMetadataRecord> {
    return this.request<import('./channel-metadata').ChannelMetadataRecord>(`/channels/${encodeURIComponent(channelId)}/metadata`);
  }

  async updateChannelMetadata(channelId: string, input: { expected_version: number; name: string; description: string }): Promise<import('./channel-metadata').ChannelMetadataRecord> {
    return this.request<import('./channel-metadata').ChannelMetadataRecord>(`/channels/${encodeURIComponent(channelId)}/metadata`, {
      method: 'PATCH', body: JSON.stringify(input),
    });
  }

  async getChannelMembers(channelId: string): Promise<ChannelMember[]> {
    return this.request<ChannelMember[]>(`/channels/${channelId}/members`);
  }

  async createChannelInvite(channelId: string): Promise<ChannelInvite> {
    return this.request(`/channels/${channelId}/invites`, { method: 'POST', body: JSON.stringify({ expires_in_hours: 24 }) });
  }

  async getChannelInvites(channelId: string): Promise<ChannelInvite[]> {
    return this.request(`/channels/${channelId}/invites`);
  }

  async revokeChannelInvite(channelId: string, inviteId: string): Promise<void> {
    await this.request(`/channels/${channelId}/invites/${inviteId}`, { method: 'DELETE' });
  }

  async acceptChannelInvite(token: string): Promise<Channel> {
    return this.request('/channels/invites/accept', { method: 'POST', body: JSON.stringify({ token }) });
  }

  async changeMemberRole(channelId: string, userId: string, role: 'admin' | 'member'): Promise<void> {
    await this.request(`/channels/${channelId}/members/${userId}`, { method: 'PATCH', body: JSON.stringify({ role }) });
  }

  async removeChannelMember(channelId: string, userId: string): Promise<void> {
    await this.request(`/channels/${channelId}/members/${userId}`, { method: 'DELETE' });
  }

  async leaveChannel(channelId: string): Promise<void> {
    await this.request(`/channels/${channelId}/leave`, { method: 'POST' });
  }

  // Messages
  async sendMessage(channelId: string, content: string, mentions: string[] = [], references: MessageReference[] = []): Promise<Message> {
    const ownerToken = this.token, ownerUser = this.userId;
    const selectedMentions = [...mentions], selectedReferences = references.map(reference => ({ ...reference }));
    const secure = await this.secureChannel(channelId);
    if (ownerToken !== this.token || ownerUser !== this.userId) throw new Error('The signed-in account changed.');
    if (secure) return this.rememberMessage(await secure.sendMessage(channelId, content, undefined, selectedMentions, selectedReferences));
    return this.request<Message>(`/messages/channels/${channelId}`, {
      method: 'POST',
      body: JSON.stringify({ content, mentions: selectedMentions, references: selectedReferences }),
    });
  }

  async getMessages(channelId: string, options: { before?: string; limit?: number; signal?: AbortSignal; rootsOnly?: boolean } = {}): Promise<Message[]> {
    const secure = await this.secureChannel(channelId);
    if (options.signal?.aborted) throw new DOMException('Cancelled', 'AbortError');
    if (secure) return (await secure.getMessages(channelId, options.before, options.limit || 50, options.rootsOnly || false)).map(message => this.rememberMessage(message));
    const params = new URLSearchParams({ limit: String(options.limit || 50) });
    if (options.before) params.set('before', options.before);
    if (options.rootsOnly) params.set('roots_only', 'true');
    return (await this.request<Message[]>(`/messages/channels/${channelId}?${params}`, { signal: options.signal })).map(message => this.rememberMessage(message));
  }

  // Files
  async uploadFile(channelId: string, file: File): Promise<FileInfo> {
    const formData = new FormData();
    formData.append('file', file);

    const headers: Record<string, string> = {};
    const requestToken = this.token;
    if (requestToken) {
      headers['Authorization'] = `Bearer ${requestToken}`;
    }

    const response = await fetch(`${API_URL}/files/channels/${channelId}`, {
      method: 'POST',
      headers,
      body: formData,
    });

    if (!response.ok) {
      if (response.status === 401 && requestToken && requestToken === this.token) window.dispatchEvent(new Event('freecollab:session-expired'));
      throw await responseError(response, 'Upload failed. Please try again.');
    }

    const data=await response.json();
    if(requestToken===this.token&&Array.isArray(data?.policy_warnings)&&data.policy_warnings.length)
      window.dispatchEvent(new CustomEvent('freecollab:policy-warning',{detail:{count:data.policy_warnings.length}}));
    return data;
  }

  async getFiles(channelId: string): Promise<FileInfo[]> {
    return this.request<FileInfo[]>(`/files/channels/${channelId}`);
  }

  getFileUrl(fileId: string): string {
    return `${API_URL}/files/${fileId}`;
  }

  async downloadFile(fileId: string): Promise<Blob> {
    const headers: Record<string, string> = {};
    const requestToken = this.token;
    if (requestToken) headers.Authorization = `Bearer ${requestToken}`;
    const response = await fetch(`${API_URL}/files/${encodeURIComponent(fileId)}`, { headers });
    if (!response.ok) {
      if (response.status === 401 && requestToken && requestToken === this.token) window.dispatchEvent(new Event('freecollab:session-expired'));
      throw await responseError(response, 'Download failed. Please try again.');
    }
    return response.blob();
  }

  async deleteFile(fileId: string): Promise<void> {
    await this.request(`/files/${encodeURIComponent(fileId)}`, { method: 'DELETE' });
  }

  // Reactions
  async addReaction(messageId: string, emoji: string): Promise<{ added: boolean }> {
    const secure = await this.secureMessage(messageId);
    if (secure) { await secure.adapter.addReaction(secure.channelId, messageId, emoji); return { added: true }; }
    return this.request(`/reactions/messages/${messageId}`, {
      method: 'POST',
      body: JSON.stringify({ emoji }),
    });
  }

  async removeReaction(messageId: string, emoji: string): Promise<{ removed: boolean }> {
    const secure = await this.secureMessage(messageId);
    if (secure) { await secure.adapter.removeReaction(secure.channelId, messageId, emoji); return { removed: true }; }
    return this.request(`/reactions/messages/${messageId}/${encodeURIComponent(emoji)}`, {
      method: 'DELETE',
    });
  }

  async getReactions(messageId: string): Promise<Reaction[]> {
    const secure = await this.secureMessage(messageId);
    if (secure) return secure.adapter.getReactions(secure.channelId, messageId);
    return this.request<Reaction[]>(`/reactions/messages/${messageId}`);
  }

  // Read Receipts
  async markAsRead(channelId: string, messageId: string): Promise<void> {
    const secure = await this.secureChannel(channelId);
    if (secure) { await secure.markRead(channelId, messageId); return; }
    await this.request(`/read-receipts/channels/${channelId}`, {
      method: 'POST',
      body: JSON.stringify({ message_id: messageId }),
    });
  }

  async getReadReceipts(channelId: string): Promise<ReadReceipt[]> {
    const secure = await this.secureChannel(channelId);
    if (secure) return secure.getReadReceipts(channelId);
    return this.request<ReadReceipt[]>(`/read-receipts/channels/${channelId}`);
  }

  async getUnreadCount(channelId: string): Promise<{ unread_count: number }> {
    const secure = await this.secureChannel(channelId);
    if (secure) {
      const receipt = (await secure.getReadReceipts(channelId)).find(row => row.user_id === this.userId);
      const rows = await secure.getMessages(channelId, undefined, 100);
      const boundary = receipt ? rows.findIndex(row => row.id === receipt.last_read_message_id) : -1;
      return { unread_count: (boundary < 0 ? rows : rows.slice(0, boundary)).filter(row => row.author_id !== this.userId && !row.deleted).length };
    }
    return this.request(`/read-receipts/channels/${channelId}/unread-count`);
  }

  // Threads
  async replyToMessage(messageId: string, content: string, mentions: string[] = [], references: MessageReference[] = []): Promise<Message> {
    const ownerToken = this.token, ownerUser = this.userId;
    const selectedMentions = [...mentions], selectedReferences = references.map(reference => ({ ...reference }));
    const secure = await this.secureMessage(messageId);
    if (ownerToken !== this.token || ownerUser !== this.userId) throw new Error('The signed-in account changed.');
    if (secure) return this.rememberMessage(await secure.adapter.sendMessage(secure.channelId, content, messageId, selectedMentions, selectedReferences));
    return this.request<Message>(`/threads/messages/${messageId}/reply`, {
      method: 'POST',
      body: JSON.stringify({ content, mentions: selectedMentions, references: selectedReferences }),
    });
  }

  async getThread(messageId: string, options: { before?: string; limit?: number; signal?: AbortSignal } = {}): Promise<{ parent_id: string; thread_count: number; replies: Message[]; has_more: boolean }> {
    const secure = await this.secureMessage(messageId);
    if (options.signal?.aborted) throw new DOMException('Cancelled', 'AbortError');
    const limit = Math.max(1, Math.min(options.limit || 50, 100));
    if (secure) {
      const all = (await secure.adapter.getThread(secure.channelId, messageId)).map(message => this.rememberMessage(message));
      if (options.signal?.aborted) throw new DOMException('Cancelled', 'AbortError');
      const end = options.before ? all.findIndex(message => message.id === options.before) : all.length;
      if (end < 0) throw new APIError('Thread cursor not found', 404);
      const start = Math.max(0, end - limit);
      return { parent_id: messageId, thread_count: all.length, replies: all.slice(start, end), has_more: start > 0 };
    }
    const query = new URLSearchParams({ limit: String(limit) });
    if (options.before) query.set('before', options.before);
    return this.request(`/threads/messages/${encodeURIComponent(messageId)}?${query}`, { signal: options.signal });
  }

  // Mentions
  async getMentionableMembers(channelId: string, query?: string): Promise<MentionableMember[]> {
    const params = query ? `?query=${encodeURIComponent(query)}` : '';
    return this.request<MentionableMember[]>(`/mentions/channels/${channelId}/members${params}`);
  }

  async getMyMentions(): Promise<Message[]> {
    return this.request<Message[]>('/mentions/me');
  }

  // Edit/Delete Messages
  async editMessage(messageId: string, content: string, references?: MessageReference[]): Promise<Message> {
    const ownerToken = this.token, ownerUser = this.userId;
    const selectedReferences = references?.map(reference => ({ ...reference }));
    const secure = await this.secureMessage(messageId);
    if (ownerToken !== this.token || ownerUser !== this.userId) throw new Error('The signed-in account changed.');
    if (secure) { const updated = await secure.adapter.editMessage(secure.channelId, messageId, content, selectedReferences); return { id: messageId, channel_id: secure.channelId, author_id: this.userId!, content, ...updated, encrypted: true, created_at: new Date().toISOString(), edited_at: new Date().toISOString() }; }
    return this.request<Message>(`/messages/${messageId}`, {
      method: 'PUT',
      body: JSON.stringify({ content, references: selectedReferences || [], ...(selectedReferences !== undefined ? { mentions: [] } : {}) }),
    });
  }

  async deleteMessage(messageId: string): Promise<{ deleted: boolean }> {
    const secure = await this.secureMessage(messageId);
    if (secure) { await secure.adapter.deleteMessage(secure.channelId, messageId); return { deleted: true }; }
    return this.request(`/messages/${messageId}`, {
      method: 'DELETE',
    });
  }

  // User lookup
  async getUserPublicKey(userId: string): Promise<{ user_id: string; public_key: string }> {
    return this.request(`/auth/users/${userId}/public-key`);
  }

  // ========== PRESENCE ==========
  async getUserPresence(userId: string): Promise<UserPresence> {
    return this.request<UserPresence>(`/presence/users/${userId}`);
  }

  async getOnlineUsers(): Promise<UserPresence[]> {
    return this.request<UserPresence[]>('/presence/online');
  }

  async getChannelPresence(channelId: string): Promise<UserPresence[]> {
    return this.request<UserPresence[]>(`/presence/channel/${channelId}`);
  }

  // ========== POSTS/FLOWS ==========
  async createPost(flowId: string, content: string): Promise<Post> {
    return this.request<Post>(`/posts/flows/${flowId}`, {
      method: 'POST',
      body: JSON.stringify({ content }),
    });
  }

  async getFlow(flowId: string, limit = 50, offset = 0): Promise<Post[]> {
    return this.request<Post[]>(`/posts/flows/${flowId}?limit=${limit}&offset=${offset}`);
  }

  async getPost(postId: string): Promise<Post> {
    return this.request<Post>(`/posts/${postId}`);
  }

  async updatePost(postId: string, content: string): Promise<Post> {
    return this.request<Post>(`/posts/${postId}`, {
      method: 'PUT',
      body: JSON.stringify({ content }),
    });
  }

  async deletePost(postId: string): Promise<{ deleted: boolean }> {
    return this.request(`/posts/${postId}`, {
      method: 'DELETE',
    });
  }

  async likePost(postId: string): Promise<{ liked: boolean; like_count: number }> {
    return this.request(`/posts/${postId}/like`, {
      method: 'POST',
    });
  }

  async unlikePost(postId: string): Promise<{ unliked: boolean; like_count: number }> {
    return this.request(`/posts/${postId}/like`, {
      method: 'DELETE',
    });
  }

  async getPostLikers(postId: string): Promise<PostLike[]> {
    return this.request<PostLike[]>(`/posts/${postId}/likes`);
  }

  // ========== COMMENTS ==========
  async createComment(entityType: string, entityId: string, content: string, parentId?: string): Promise<Comment> {
    return this.request<Comment>(`/comments/${entityType}/${entityId}`, {
      method: 'POST',
      body: JSON.stringify({ content, parent_id: parentId }),
    });
  }

  async getComments(entityType: string, entityId: string): Promise<Comment[]> {
    return this.request<Comment[]>(`/comments/${entityType}/${entityId}`);
  }

  async getCommentReplies(commentId: string): Promise<Comment[]> {
    return this.request<Comment[]>(`/comments/${commentId}/replies`);
  }

  async updateComment(commentId: string, content: string): Promise<Comment> {
    return this.request<Comment>(`/comments/${commentId}`, {
      method: 'PUT',
      body: JSON.stringify({ content }),
    });
  }

  async deleteComment(commentId: string): Promise<{ deleted: boolean }> {
    return this.request(`/comments/${commentId}`, {
      method: 'DELETE',
    });
  }

  async likeComment(commentId: string): Promise<{ liked: boolean; like_count: number }> {
    return this.request(`/comments/${commentId}/like`, {
      method: 'POST',
    });
  }

  async unlikeComment(commentId: string): Promise<{ unliked: boolean; like_count: number }> {
    return this.request(`/comments/${commentId}/like`, {
      method: 'DELETE',
    });
  }

  // ========== NOTIFICATIONS ==========
  async getNotifications(limit = 50, offset = 0, unreadOnly = false): Promise<Notification[]> {
    return this.request<Notification[]>(`/notifications?limit=${limit}&offset=${offset}&unread_only=${unreadOnly}`);
  }

  async getUnreadNotificationCount(): Promise<{ unread_count: number }> {
    return this.request('/notifications/unread-count');
  }

  async markNotificationRead(notificationId: string): Promise<{ marked_read: boolean }> {
    return this.request(`/notifications/${notificationId}/read`, {
      method: 'POST',
    });
  }

  async markAllNotificationsRead(): Promise<{ marked_all_read: boolean }> {
    return this.request('/notifications/read-all', {
      method: 'POST',
    });
  }

  async getNotificationPreferences(): Promise<NotificationPreference> {
    return this.request<NotificationPreference>('/notifications/preferences');
  }

  async updateNotificationPreferences(prefs: Partial<NotificationPreference>): Promise<{ updated: boolean }> {
    return this.request<{ updated: boolean }>('/notifications/preferences', {
      method: 'PUT',
      body: JSON.stringify(prefs),
    });
  }
}

interface FileInfo {
  id: string;
  channel_id: string;
  uploader_id: string;
  filename: string;
  content_type: string;
  size: number;
  encrypted: boolean;
  created_at: string;
  document_id?: string;
  document_owner_id?: string;
  version_number?: number;
  version_count?: number;
}

export const api = new FreeCollabAPI();
export type { 
  Token, MFAChallenge, User, Channel, ChannelInvite, Message, ChannelMember, FileInfo, Reaction, ReadReceipt,
  ThreadReply, MentionableMember, UserPresence, Post, PostLike, Comment, 
  Notification, NotificationPreference 
};
