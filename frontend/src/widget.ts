/**
 * FreeCollab Embeddable Widget
 * 
 * A lightweight, embeddable chat widget that can be added to any website.
 * Uses the server's authenticated message API and polls the latest 50 messages.
 * The server processes message plaintext; use HTTPS for deployed integrations.
 * 
 * Usage:
 * <script src="https://your-freecollab-server.com/widget.js"></script>
 * <script>
 *   FreeCollab.init({
 *     apiUrl: 'https://your-freecollab-server.com',
 *     channelId: 'your-channel-id',
 *     position: 'bottom-right',
 *     theme: 'light'
 *   });
 * </script>
 */

interface WidgetConfig {
  apiUrl: string;
  channelId?: string;
  position?: 'bottom-right' | 'bottom-left' | 'top-right' | 'top-left';
  theme?: 'light' | 'dark';
  primaryColor?: string;
  title?: string;
  subtitle?: string;
  welcomeMessage?: string;
  userToken?: string;
  onReady?: () => void;
  onMessage?: (message: Message) => void;
  onError?: (error: Error) => void;
}

interface Message {
  id: string;
  content: string;
  author_id: string;
  created_at: string;
}

const DEFAULT_CONFIG: Partial<WidgetConfig> = {
  position: 'bottom-right',
  theme: 'light',
  primaryColor: '#8B5CF6',
  title: 'Chat with us',
  subtitle: 'We typically reply within minutes',
  welcomeMessage: 'Hello! How can we help you today?',
};

class FreeCollabWidget {
  private config: WidgetConfig;
  private container: HTMLDivElement | null = null;
  private isOpen: boolean = false;
  private unreadCount: number = 0;
  private style: HTMLStyleElement | null = null;
  private pollTimer: ReturnType<typeof setTimeout> | null = null;
  private pollController: AbortController | null = null;
  private generation = 0;
  private seenMessageIds = new Set<string>();
  private currentUserId: string | null = null;
  private sending = false;
  private destroyed = false;

  constructor(config: WidgetConfig) {
    this.config = { ...DEFAULT_CONFIG, ...config };
    // Restrict interpolated CSS to a color token, never arbitrary stylesheet text.
    if (!/^#[0-9a-f]{3,8}$/i.test(this.config.primaryColor || '')) {
      this.config.primaryColor = DEFAULT_CONFIG.primaryColor;
    }
    if (!['bottom-right', 'bottom-left', 'top-right', 'top-left'].includes(this.config.position || '')) {
      this.config.position = DEFAULT_CONFIG.position;
    }
    this.config.theme = this.config.theme === 'dark' ? 'dark' : 'light';
    this.init();
  }

  private init() {
    this.injectStyles();
    this.createWidget();
    this.setupMessageHandler();
    
    if (this.config.onReady) {
      this.config.onReady();
    }
  }

  private injectStyles() {
    const style = document.createElement('style');
    style.textContent = `
      .freecollab-widget-container {
        position: fixed;
        z-index: 999999;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      }
      .freecollab-widget-container.bottom-right {
        bottom: 20px;
        right: 20px;
      }
      .freecollab-widget-container.bottom-left {
        bottom: 20px;
        left: 20px;
      }
      .freecollab-widget-container.top-right {
        top: 20px;
        right: 20px;
      }
      .freecollab-widget-container.top-left {
        top: 20px;
        left: 20px;
      }
      .freecollab-launcher {
        width: 60px;
        height: 60px;
        border-radius: 50%;
        background: linear-gradient(135deg, ${this.config.primaryColor} 0%, #EC4899 100%);
        border: none;
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: center;
        box-shadow: 0 4px 20px rgba(0,0,0,0.2);
        transition: transform 0.2s ease, box-shadow 0.2s ease;
        position: relative;
      }
      .freecollab-launcher:hover {
        transform: scale(1.05);
        box-shadow: 0 6px 25px rgba(0,0,0,0.25);
      }
      .freecollab-launcher svg {
        width: 28px;
        height: 28px;
        fill: white;
      }
      .freecollab-badge {
        position: absolute;
        top: -5px;
        right: -5px;
        background: #EF4444;
        color: white;
        font-size: 12px;
        font-weight: 600;
        width: 22px;
        height: 22px;
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        border: 2px solid white;
      }
      .freecollab-chat-window {
        display: none;
        width: 380px;
        height: 550px;
        background: white;
        border-radius: 16px;
        box-shadow: 0 10px 40px rgba(0,0,0,0.15);
        overflow: hidden;
        flex-direction: column;
        margin-bottom: 15px;
      }
      .freecollab-chat-window.open {
        display: flex;
      }
      .freecollab-chat-window.dark {
        background: #1e293b;
        color: white;
      }
      .freecollab-header {
        padding: 20px;
        background: linear-gradient(135deg, ${this.config.primaryColor} 0%, #EC4899 100%);
        color: white;
      }
      .freecollab-header h3 {
        margin: 0;
        font-size: 18px;
        font-weight: 600;
      }
      .freecollab-header p {
        margin: 5px 0 0;
        font-size: 13px;
        opacity: 0.9;
      }
      .freecollab-header-actions {
        display: flex;
        gap: 8px;
        margin-top: 10px;
      }
      .freecollab-header-btn {
        background: rgba(255,255,255,0.2);
        border: none;
        color: white;
        padding: 6px 12px;
        border-radius: 6px;
        font-size: 12px;
        cursor: pointer;
        display: flex;
        align-items: center;
        gap: 5px;
      }
      .freecollab-header-btn:hover {
        background: rgba(255,255,255,0.3);
      }
      .freecollab-messages {
        flex: 1;
        overflow-y: auto;
        padding: 20px;
        display: flex;
        flex-direction: column;
        gap: 12px;
      }
      .freecollab-message {
        max-width: 80%;
        padding: 12px 16px;
        border-radius: 16px;
        font-size: 14px;
        line-height: 1.4;
      }
      .freecollab-message.received {
        background: #f1f5f9;
        align-self: flex-start;
        border-bottom-left-radius: 4px;
      }
      .freecollab-message.sent {
        background: linear-gradient(135deg, ${this.config.primaryColor} 0%, #EC4899 100%);
        color: white;
        align-self: flex-end;
        border-bottom-right-radius: 4px;
      }
      .freecollab-message-time {
        font-size: 11px;
        opacity: 0.7;
        margin-top: 4px;
      }
      .freecollab-input-area {
        padding: 15px 20px;
        border-top: 1px solid #e2e8f0;
        display: flex;
        gap: 10px;
      }
      .freecollab-input {
        flex: 1;
        padding: 12px 16px;
        border: 1px solid #e2e8f0;
        border-radius: 24px;
        font-size: 14px;
        outline: none;
        transition: border-color 0.2s;
      }
      .freecollab-input:focus {
        border-color: ${this.config.primaryColor};
      }
      .freecollab-send-btn {
        width: 44px;
        height: 44px;
        border-radius: 50%;
        background: linear-gradient(135deg, ${this.config.primaryColor} 0%, #EC4899 100%);
        border: none;
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: center;
        transition: transform 0.2s;
      }
      .freecollab-send-btn:hover {
        transform: scale(1.05);
      }
      .freecollab-send-btn svg {
        width: 20px;
        height: 20px;
        fill: white;
      }
      .freecollab-powered {
        text-align: center;
        padding: 10px;
        font-size: 11px;
        color: #94a3b8;
      }
      .freecollab-powered a {
        color: ${this.config.primaryColor};
        text-decoration: none;
      }
      .freecollab-encryption-badge {
        display: flex;
        align-items: center;
        gap: 5px;
        font-size: 11px;
        color: #10b981;
        padding: 10px 20px;
        background: #f0fdf4;
      }
      @media (max-width: 480px) {
        .freecollab-chat-window {
          width: 100%;
          height: 100%;
          position: fixed;
          top: 0;
          left: 0;
          border-radius: 0;
          margin: 0;
        }
      }
    `;
    document.head.appendChild(style);
    this.style = style;
  }

  private createWidget() {
    // Create container
    this.container = document.createElement('div');
    this.container.className = `freecollab-widget-container ${this.config.position}`;

    // Create chat window
    const chatWindow = document.createElement('div');
    chatWindow.className = `freecollab-chat-window ${this.config.theme}`;
    chatWindow.innerHTML = `
      <div class="freecollab-header">
        <h3></h3>
        <p></p>
        <div class="freecollab-header-actions">
          <button class="freecollab-header-btn" type="button">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor">
              <path d="M19 13H5v-2h14v2z"/>
            </svg>
            Minimize
          </button>
        </div>
      </div>
      <div class="freecollab-encryption-badge">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor">
          <path d="M18 8h-1V6c0-2.76-2.24-5-5-5S7 3.24 7 6v2H6c-1.1 0-2 .9-2 2v10c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V10c0-1.1-.9-2-2-2zm-6 9c-1.1 0-2-.9-2-2s.9-2 2-2 2 .9 2 2-.9 2-2 2zm3.1-9H8.9V6c0-1.71 1.39-3.1 3.1-3.1 1.71 0 3.1 1.39 3.1 3.1v2z"/>
        </svg>
        Messages are processed by the server
      </div>
      <div class="freecollab-messages" role="log" aria-label="Messages"></div>
      <div class="freecollab-status" role="status" style="padding: 0 20px; font-size: 12px;"></div>
      <div class="freecollab-input-area">
        <input type="text" class="freecollab-input" aria-label="Message" placeholder="Type a message..." />
        <button class="freecollab-send-btn" type="button" aria-label="Send message">
          <svg viewBox="0 0 24 24">
            <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/>
          </svg>
        </button>
      </div>
      <div class="freecollab-powered">
        Powered by <a href="https://github.com/freecollab-eu/freecollab" target="_blank" rel="noopener noreferrer">FreeCollab</a>
      </div>
    `;
    chatWindow.querySelector('h3')!.textContent = this.config.title || '';
    chatWindow.querySelector('.freecollab-header p')!.textContent = this.config.subtitle || '';
    chatWindow.querySelector('.freecollab-header-btn')!.addEventListener('click', () => this.minimize());
    chatWindow.querySelector('.freecollab-send-btn')!.addEventListener('click', () => { void this.sendMessage(); });
    if (this.config.welcomeMessage) {
      const welcome = document.createElement('div');
      welcome.className = 'freecollab-message received';
      welcome.textContent = this.config.welcomeMessage;
      chatWindow.querySelector('.freecollab-messages')!.appendChild(welcome);
    }

    // Create launcher button
    const launcher = document.createElement('button');
    launcher.className = 'freecollab-launcher';
    launcher.innerHTML = `
      <svg viewBox="0 0 24 24">
        <path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm0 14H6l-2 2V4h16v12z"/>
      </svg>
      <span class="freecollab-badge" style="display: none;">0</span>
    `;
    launcher.onclick = () => this.toggle();

    this.container.appendChild(chatWindow);
    this.container.appendChild(launcher);
    document.body.appendChild(this.container);

    // Setup input handler
    const input = this.container.querySelector('.freecollab-input') as HTMLInputElement;
    input?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.isComposing) {
        void this.sendMessage();
      }
    });

    // Retained for existing integrations; widget controls use scoped listeners.
    (window as any).FreeCollabInstance = this;
  }

  private setupMessageHandler() {
    const generation = ++this.generation;
    if (this.pollTimer) clearTimeout(this.pollTimer);
    this.pollController?.abort();
    this.currentUserId = null;
    this.seenMessageIds.clear();
    const messages = this.container?.querySelector('.freecollab-messages');
    messages?.replaceChildren();
    if (messages && this.config.welcomeMessage) {
      const welcome = document.createElement('div');
      welcome.className = 'freecollab-message received';
      welcome.textContent = this.config.welcomeMessage;
      messages.appendChild(welcome);
    }
    this.clearUnread();
    if (!this.config.userToken || !this.config.channelId) {
      this.setStatus('Chat requires a user token and channel.');
      return;
    }
    this.setStatus('Connecting…');
    void this.pollMessages(generation);
  }

  private setStatus(message: string) {
    const status = this.container?.querySelector('.freecollab-status');
    if (status) status.textContent = message;
  }

  private reportError(error: unknown) {
    const failure = error instanceof Error ? error : new Error('Chat request failed');
    this.setStatus(failure.message);
    this.config.onError?.(failure);
  }

  private async pollMessages(generation: number) {
    const controller = new AbortController();
    this.pollController = controller;
    const headers = { Authorization: `Bearer ${this.config.userToken}` };
    try {
      const apiUrl = this.config.apiUrl.replace(/\/$/, '');
      if (!this.currentUserId) {
        const identity = await fetch(`${apiUrl}/auth/me`, { headers, signal: controller.signal });
        if (!identity.ok) throw new Error(`Could not authenticate chat (${identity.status}).`);
        const user = await identity.json();
        if (generation !== this.generation || this.destroyed) return;
        this.currentUserId = user.id;
      }
      const response = await fetch(`${apiUrl}/messages/channels/${encodeURIComponent(this.config.channelId!)}?limit=50`, {
        headers, signal: controller.signal,
      });
      if (!response.ok) throw new Error(`Could not load messages (${response.status}).`);
      const messages: Message[] = await response.json();
      if (generation !== this.generation || this.destroyed) return;
      // API returns newest first. Initial history is not an unread notification.
      const initial = this.seenMessageIds.size === 0;
      for (const message of messages.reverse()) {
        this.addMessage(message, message.author_id === this.currentUserId, !initial);
      }
      this.setStatus('');
    } catch (error) {
      if (!controller.signal.aborted && generation === this.generation && !this.destroyed) this.reportError(error);
    } finally {
      if (generation === this.generation && !this.destroyed && !controller.signal.aborted) {
        this.pollTimer = setTimeout(() => { void this.pollMessages(generation); }, 3000);
      }
    }
  }

  public toggle() {
    this.isOpen = !this.isOpen;
    const chatWindow = this.container?.querySelector('.freecollab-chat-window');
    if (chatWindow) {
      chatWindow.classList.toggle('open', this.isOpen);
    }
    if (this.isOpen) {
      this.clearUnread();
    }
  }

  public open() {
    this.isOpen = true;
    const chatWindow = this.container?.querySelector('.freecollab-chat-window');
    chatWindow?.classList.add('open');
    this.clearUnread();
  }

  public minimize() {
    this.isOpen = false;
    const chatWindow = this.container?.querySelector('.freecollab-chat-window');
    chatWindow?.classList.remove('open');
  }

  public close() {
    this.minimize();
  }

  private clearUnread() {
    this.unreadCount = 0;
    const badge = this.container?.querySelector('.freecollab-badge') as HTMLElement;
    if (badge) {
      badge.style.display = 'none';
    }
  }

  private updateUnread(count: number) {
    this.unreadCount = count;
    const badge = this.container?.querySelector('.freecollab-badge') as HTMLElement;
    if (badge) {
      badge.textContent = count.toString();
      badge.style.display = count > 0 ? 'flex' : 'none';
    }
  }

  public async sendMessage() {
    const input = this.container?.querySelector('.freecollab-input') as HTMLInputElement | null;
    const content = input?.value.trim();
    if (!content || this.sending || this.destroyed) return;
    if (!this.config.userToken || !this.config.channelId) {
      this.reportError(new Error('Chat requires a user token and channel.'));
      return;
    }
    const generation = this.generation;
    const button = this.container?.querySelector('.freecollab-send-btn') as HTMLButtonElement | null;
    this.sending = true;
    if (button) button.disabled = true;
    this.setStatus('Sending…');
    try {
      const response = await fetch(`${this.config.apiUrl.replace(/\/$/, '')}/messages/channels/${encodeURIComponent(this.config.channelId)}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${this.config.userToken}`,
        },
        body: JSON.stringify({ content }),
      });
      if (!response.ok) throw new Error(`Message was not sent (${response.status}). Please retry.`);
      const message: Message = await response.json();
      if (generation !== this.generation || this.destroyed) return;
      // POST returns the stored ciphertext; display the submitted text after acknowledgement.
      this.addMessage({ ...message, content }, true, false);
      if (input && input.value.trim() === content) input.value = '';
      this.setStatus('');
    } catch (error) {
      if (generation === this.generation && !this.destroyed) this.reportError(error);
    } finally {
      this.sending = false;
      if (button) button.disabled = false;
    }
  }

  private addMessage(message: Message, sent: boolean = false, notify: boolean = true) {
    const messagesContainer = this.container?.querySelector('.freecollab-messages');
    if (!messagesContainer || this.destroyed || this.seenMessageIds.has(message.id)) return;
    this.seenMessageIds.add(message.id);

    const messageEl = document.createElement('div');
    messageEl.className = `freecollab-message ${sent ? 'sent' : 'received'}`;
    messageEl.textContent = message.content;
    const time = document.createElement('div');
    time.className = 'freecollab-message-time';
    time.textContent = new Date(message.created_at).toLocaleTimeString();
    messageEl.appendChild(time);

    messagesContainer.appendChild(messageEl);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;

    if (this.config.onMessage) {
      this.config.onMessage(message);
    }

    if (notify && !this.isOpen && !sent) {
      this.updateUnread(this.unreadCount + 1);
    }
  }

  public receiveMessage(message: Message) {
    this.addMessage(message, false);
  }

  public setUser(token: string) {
    this.config.userToken = token;
    this.setupMessageHandler();
  }

  public setChannel(channelId: string) {
    this.config.channelId = channelId;
    this.setupMessageHandler();
  }

  public destroy() {
    this.destroyed = true;
    ++this.generation;
    if (this.pollTimer) clearTimeout(this.pollTimer);
    this.pollController?.abort();
    this.container?.remove();
    this.style?.remove();
    if ((window as any).FreeCollabInstance === this) delete (window as any).FreeCollabInstance;
  }
}

// Export for module usage
export { FreeCollabWidget };
export type { WidgetConfig, Message };

// Export init as well as assigning it for unbundled usage: the IIFE build writes
// its module exports to window.FreeCollab after this module has evaluated.
export function init(config: WidgetConfig) {
  return new FreeCollabWidget(config);
}

(window as any).FreeCollab = { init };
