import { t, useI18n } from '../i18n';
import { useState } from 'react'
import { ChevronRight, ChevronDown, Book, Code, Zap, Shield, MessageCircle, ArrowLeft } from 'lucide-react'
import '../styles/panels.css'
import { Brand } from './Brand'

interface DocsProps {
  onBack: () => void
}

const sections = [
  {
    id: 'getting-started',
    title: 'Getting Started',
    icon: Zap,
    items: [
      { id: 'introduction', title: 'Introduction' },
      { id: 'installation', title: 'Installation' },
      { id: 'quick-start', title: 'Quick Start' },
    ]
  },
  {
    id: 'features',
    title: 'Core Features',
    icon: MessageCircle,
    items: [
      { id: 'real-time-chat', title: 'Real-time Chat' },
      { id: 'threads', title: 'Threads & Replies' },
      { id: 'reactions', title: 'Reactions' },
      { id: 'mentions', title: '@Mentions' },
      { id: 'presence', title: 'Presence & Typing' },
      { id: 'read-receipts', title: 'Read Receipts' },
    ]
  },
  {
    id: 'flows',
    title: 'Flows & Comments',
    icon: Book,
    items: [
      { id: 'flows-overview', title: 'Flows Overview' },
      { id: 'posts', title: 'Creating Posts' },
      { id: 'comments', title: 'Comments System' },
    ]
  },
  {
    id: 'admin',
    title: 'Administration',
    icon: Shield,
    items: [
      { id: 'admin-panel', title: 'Admin Panel' },
      { id: 'user-management', title: 'User Management' },
      { id: 'roles', title: 'Roles & Permissions' },
      { id: 'moderation', title: 'Moderation Tools' },
    ]
  },
  {
    id: 'security',
    title: 'Security',
    icon: Shield,
    items: [
      { id: 'encryption', title: 'Security Model' },
      { id: 'authentication', title: 'Authentication' },
    ]
  },
  {
    id: 'api',
    title: 'API Reference',
    icon: Code,
    items: [
      { id: 'rest-api', title: 'REST API' },
      { id: 'websocket', title: 'WebSocket Events' },
      { id: 'react-sdk', title: 'React Integration' },
    ]
  },
]

const content: Record<string, { title: string; content: () => React.ReactNode }> = {
  'introduction': {
    title: 'Introduction',
    content: () => (
      <div className="space-y-6">
        <p className="text-lg fc-muted">
          {t('FreeCollab is an MIT-licensed workspace for conversations, documents, files, projects and meetings. Run it on Railway or your own infrastructure, and connect your organization’s own identity and services.')}</p>
        <div className="fc-docs-callout">
          <h3 className="font-semibold fc-accent mb-2">{t("Why FreeCollab?")}</h3>
          <ul className="space-y-2 fc-muted">
            <li className="flex items-center gap-2">
              <span className="fc-accent">✓</span> {t(" Self-hosted - Your data stays on your servers")}</li>
            <li className="flex items-center gap-2">
              <span className="fc-accent">✓</span> {t(" Server-side message encryption - The server handles message content")}</li>
            <li className="flex items-center gap-2">
              <span className="fc-accent">✓</span> {t('No per-seat software fee; hosting and provider costs are yours.')}</li>
            <li className="flex items-center gap-2">
              <span className="fc-accent">✓</span> {t(" MIT Licensed - Use it however you want")}</li>
          </ul>
        </div>
        <h3 className="text-xl font-semibold mt-8">{t("Features Overview")}</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          {['Real-time Chat', 'Threads', 'Reactions', '@Mentions', 'Read Receipts', 'Typing Indicators', 
            'Presence', 'Flows', 'Comments', 'Notifications', 'File Uploads', 'Server-side Message Encryption',
            'Admin Panel', 'Moderation', 'Self-hosted', 'Free Forever'].map((f, i) => (
            <div key={i} className="flex items-center gap-2 fc-muted">
              <span className="fc-accent">•</span> {f}
            </div>
          ))}
        </div>
      </div>
    )
  },
  'installation': {
    title: 'Installation',
    content: () => (
      <div className="space-y-6">
        <div className="fc-docs-callout"><h3>{t('Deploy on Railway or host it yourself')}</h3><p className="fc-muted">{t('Use the maintained deployment guide for private services, persistent storage, independent keys and background workers. Configure your own Microsoft 365 and Google apps through the connection guide.')}</p><div className="flex flex-wrap gap-4"><a className="fc-account-link" href="https://github.com/freecollab-eu/freecollab/blob/main/RAILWAY_DEPLOY.md" target="_blank" rel="noopener noreferrer">{t('Railway deployment guide')}</a><a className="fc-account-link" href="https://github.com/freecollab-eu/freecollab/blob/main/docs/CONNECTION_SETUP.md" target="_blank" rel="noopener noreferrer">{t('Customer connection guide')}</a></div></div>
        <h3 className="text-xl font-semibold">{t("Prerequisites")}</h3>
        <ul className="list-disc list-inside fc-muted space-y-1">
          <li>{t("Node.js 20.19+ or 22.12+")}</li>
          <li>{t("Python 3.11+")}</li>
          <li>{t("Docker (optional)")}</li>
        </ul>

        <h3 className="text-xl font-semibold mt-8">{t("Option 1: Docker pilot")}</h3>
        <div className="fc-docs-code">
          <div className="fc-docs-code-label">{t("terminal")}</div>
          <pre className="p-4 text-sm overflow-x-auto">
            <code className="fc-muted">{`git clone https://github.com/freecollab-eu/freecollab
cd freecollab
python scripts/pilot-init.py
docker compose --env-file pilot.env --project-name freecollab up --build -d --wait
# See docs/OPERATIONS.md for access and deployment settings`}</code>
          </pre>
        </div>

        <h3 className="text-xl font-semibold mt-8">{t("Option 2: Manual Setup")}</h3>
        <div className="fc-docs-code">
          <div className="fc-docs-code-label">{t("terminal")}</div>
          <pre className="p-4 text-sm overflow-x-auto">
            <code className="fc-muted">{`# Backend
cd backend
python -m venv venv
source venv/bin/activate  # or .\\venv\\Scripts\\Activate.ps1 on Windows
pip install -r requirements.txt
uvicorn app.main:socket_app --reload --port 8000

# Frontend (new terminal)
cd frontend
npm ci
npm run dev`}</code>
          </pre>
        </div>

        <h3 className="text-xl font-semibold mt-8">{t("Browser integration")}</h3>
        <p className="fc-muted">
          {t('The repository includes the React application, a widget and the locally packable @freecollab/components Web Components and React SDK. Review the package’s supported features and authentication contract before embedding it.')}</p>
      </div>
    )
  },
  'quick-start': {
    title: 'Quick Start',
    content: () => (
      <div className="space-y-6">
        <p className="fc-muted">
          {t("Start the backend and frontend using Installation, then register two accounts in the application. To integrate from another browser app, use a user-scoped token returned by login.")}</p>
        <h3 className="text-xl font-semibold">{t("Create a channel and send a message")}</h3>
        <div className="fc-docs-code">
          <pre className="p-4 text-sm overflow-x-auto">
            <code className="fc-muted">{`const apiUrl = 'http://localhost:8000' // Use HTTPS in deployment
const headers = {
  'Content-Type': 'application/json',
  Authorization: 'Bearer ' + userToken,
}

const response = await fetch(apiUrl + '/channels', {
  method: 'POST', headers,
  body: JSON.stringify({ name: 'Team', type: 'group', member_ids: [] }),
})
if (!response.ok) throw new Error('Channel creation failed')
const channel = await response.json()

const sent = await fetch(apiUrl + '/messages/channels/' + channel.id, {
  method: 'POST', headers,
  body: JSON.stringify({ content: 'Hello, team!' }),
})
if (!sent.ok) throw new Error('Message delivery failed')`}</code>
          </pre>
        </div>
        <p className="fc-muted">
          {t("Include existing member user IDs in member_ids when creating a shared channel. A third user has no access without membership. The API receives message text; this flow does not provide end-to-end encryption.")}</p>
      </div>
    )
  },
  'real-time-chat': {
    title: 'Real-time Chat',
    content: () => (
      <div className="space-y-6">
        <p className="fc-muted">
          {t("FreeCollab uses Socket.IO for realtime message delivery. Delivery time depends on your deployment and network.")}</p>

        <h3 className="text-xl font-semibold">{t("Sending Messages")}</h3>
        <div className="fc-docs-code">
          <div className="fc-docs-code-label">{t("API")}</div>
          <pre className="p-4 text-sm overflow-x-auto">
            <code className="fc-muted">{`// Send a message
await api.sendMessage(channelId, "Hello, world!")

// Messages are automatically broadcast to all channel members
// via WebSocket events`}</code>
          </pre>
        </div>

        <h3 className="text-xl font-semibold mt-8">{t("WebSocket Events")}</h3>
        <div className="fc-docs-code">
          <div className="fc-docs-code-label">{t("Events")}</div>
          <pre className="p-4 text-sm overflow-x-auto">
            <code className="fc-muted">{`// Listen for new messages
socket.on('message', (msg) => {
  console.log('New message:', msg)
})

// Listen for message edits
socket.on('message_edited', (data) => {
  console.log('Message edited:', data.id)
})

// Listen for message deletions
socket.on('message_deleted', (data) => {
  console.log('Message deleted:', data.id)
})`}</code>
          </pre>
        </div>
      </div>
    )
  },
  'threads': {
    title: 'Threads & Replies',
    content: () => (
      <div className="space-y-6">
        <p className="fc-muted">
          {t("Organize conversations with threaded replies. Keep discussions focused without cluttering the main channel.")}</p>

        <h3 className="text-xl font-semibold">{t("Creating a Thread")}</h3>
        <div className="fc-docs-code">
          <pre className="p-4 text-sm overflow-x-auto">
            <code className="fc-muted">{`// Reply to a message to create/continue a thread
await api.replyToMessage(parentMessageId, "This is a reply!")

// Get all replies in a thread
const thread = await api.getThread(parentMessageId)
console.log(thread.replies)`}</code>
          </pre>
        </div>
      </div>
    )
  },
  'reactions': {
    title: 'Reactions',
    content: () => (
      <div className="space-y-6">
        <p className="fc-muted">
          {t("Let users react to messages with emojis. Supports any emoji.")}</p>

        <h3 className="text-xl font-semibold">{t("Adding Reactions")}</h3>
        <div className="fc-docs-code">
          <pre className="p-4 text-sm overflow-x-auto">
            <code className="fc-muted">{`// Add a reaction
await api.addReaction(messageId, "👍")

// Remove a reaction
await api.removeReaction(messageId, "👍")

// Get reactions for a message
const reactions = await api.getReactions(messageId)`}</code>
          </pre>
        </div>
      </div>
    )
  },
  'mentions': {
    title: '@Mentions',
    content: () => (
      <div className="space-y-6">
        <p className="fc-muted">
          {t("Mention users with @username to notify them directly.")}</p>
        <div className="fc-docs-code">
          <pre className="p-4 text-sm overflow-x-auto">
            <code className="fc-muted">{`// Get mentionable members
const members = await api.getMentionableMembers(channelId, "search")

// Mentioned users receive a WebSocket event
socket.on('mention', (data) => {
  showNotification(\`@\${data.author_name} mentioned you\`)
})`}</code>
          </pre>
        </div>
      </div>
    )
  },
  'presence': {
    title: 'Presence & Typing',
    content: () => (
      <div className="space-y-6">
        <p className="fc-muted">
          {t("Show who's online and who's currently typing.")}</p>
        <h3 className="text-xl font-semibold">{t("Typing Indicators")}</h3>
        <div className="fc-docs-code">
          <pre className="p-4 text-sm overflow-x-auto">
            <code className="fc-muted">{`// Emit typing status
socket.emit('typing', { channel_id: channelId })
socket.emit('stop_typing', { channel_id: channelId })

// Listen for typing events
socket.on('typing', (data) => {
  showTypingIndicator(data.user_name)
})`}</code>
          </pre>
        </div>
      </div>
    )
  },
  'read-receipts': {
    title: 'Read Receipts',
    content: () => (
      <div className="space-y-6">
        <p className="fc-muted">
          {t("Track which messages have been read by users.")}</p>
        <div className="fc-docs-code">
          <pre className="p-4 text-sm overflow-x-auto">
            <code className="fc-muted">{`// Mark messages as read
await api.markAsRead(channelId, lastMessageId)

// Get unread count
const { unread_count } = await api.getUnreadCount(channelId)`}</code>
          </pre>
        </div>
      </div>
    )
  },
  'flows-overview': {
    title: 'Flows Overview',
    content: () => (
      <div className="space-y-6">
        <p className="fc-muted">
          {t("Flows are activity feeds for sharing updates, announcements, and posts with your team. Think of them as a combination of social feeds and announcement boards.")}</p>
        <div className="fc-docs-code">
          <pre className="p-4 text-sm overflow-x-auto">
            <code className="fc-muted">{`// A channel serves as the flow context
const posts = await api.getFlow(channelId)`}</code>
          </pre>
        </div>
      </div>
    )
  },
  'posts': {
    title: 'Creating Posts',
    content: () => (
      <div className="space-y-6">
        <p className="fc-muted">
          {t("Posts are the content items within a Flow.")}</p>
        <div className="fc-docs-code">
          <pre className="p-4 text-sm overflow-x-auto">
            <code className="fc-muted">{`// Create a post
await api.createPost(flowId, "Check out this update!")

// Get posts in a flow
const posts = await api.getFlow(flowId)`}</code>
          </pre>
        </div>
      </div>
    )
  },
  'comments': {
    title: 'Comments System',
    content: () => (
      <div className="space-y-6">
        <p className="fc-muted">
          {t("Add comments to posts, files, or any other content.")}</p>
        <div className="fc-docs-code">
          <pre className="p-4 text-sm overflow-x-auto">
            <code className="fc-muted">{`// Add a comment
await api.createComment("post", postId, "Great post!")

// Get comments
const comments = await api.getComments("post", postId)`}</code>
          </pre>
        </div>
      </div>
    )
  },
  'admin-panel': {
    title: 'Admin Panel',
    content: () => (
      <div className="space-y-6">
        <p className="fc-muted">
          {t("Full-featured admin panel for managing your FreeCollab instance.")}</p>
        <ul className="space-y-2 fc-muted">
          <li>{t("• View system statistics")}</li>
          <li>{t("• Manage users and roles")}</li>
          <li>{t("• Configure channels")}</li>
          <li>{t("• Monitor activity")}</li>
        </ul>
      </div>
    )
  },
  'user-management': {
    title: 'User Management',
    content: () => (
      <div className="space-y-6">
        <p className="fc-muted">{t("Create, update, and manage users.")}</p>
        <div className="fc-docs-code">
          <pre className="p-4 text-sm overflow-x-auto">
            <code className="fc-muted">{`// Admin: Create a user
POST /admin/users
{
  "email": "user@example.com",
  "password": "SecurePass123!",
  "name": "John Doe",
  "role": "user"
}

// Admin: List all users
GET /admin/users`}</code>
          </pre>
        </div>
      </div>
    )
  },
  'roles': {
    title: 'Roles & Permissions',
    content: () => (
      <div className="space-y-6">
        <p className="fc-muted">{t("FreeCollab has a built-in role system:")}</p>
        <div className="space-y-4">
          <div className="fc-panel-card">
            <h4 className="font-semibold fc-accent">{t("Admin")}</h4>
            <p className="text-sm fc-muted">{t("Full access. Max 5 admins per instance.")}</p>
          </div>
          <div className="fc-panel-card">
            <h4 className="font-semibold fc-accent">{t("Moderator")}</h4>
            <p className="text-sm fc-muted">{t("Can pause/block users, delete content. Unlimited moderators.")}</p>
          </div>
          <div className="fc-panel-card">
            <h4 className="font-semibold fc-text">{t("User")}</h4>
            <p className="text-sm fc-muted">{t("Standard user. Can chat, react, post.")}</p>
          </div>
        </div>
      </div>
    )
  },
  'moderation': {
    title: 'Moderation Tools',
    content: () => (
      <div className="space-y-6">
        <p className="fc-muted">{t("Tools for keeping your community safe.")}</p>
        <ul className="space-y-2 fc-muted">
          <li>{t("• Block/unblock users")}</li>
          <li>{t("• Delete messages")}</li>
          <li>{t("• Pause user activity")}</li>
          <li>{t("• View user history")}</li>
        </ul>
      </div>
    )
  },
  'encryption': {
    title: 'Security Model',
    content: () => (
      <div className="space-y-6">
        <div className="fc-docs-callout"><h3>{t('Two explicit channel models')}</h3><p>{t('Native channels use server-side message encryption. End-to-end encrypted Matrix channels use trusted client devices, device verification and separate recovery keys. Choose the channel model deliberately; enabling server encryption does not convert native history to end-to-end encryption.')}</p><a className="fc-account-link" href="https://github.com/freecollab-eu/freecollab/blob/main/docs/SECURITY_MODEL.md" target="_blank" rel="noopener noreferrer">{t('Read the complete security model')}</a></div>
        <p className="fc-muted">
          {t('In native channels, the server encrypts message bodies before database storage and can read the plaintext. Native uploaded files remain readable on the storage volume. These native-channel boundaries differ from the client-encrypted Matrix path.')}</p>
        <div className="fc-docs-callout">
          <h4 className="font-semibold fc-accent mb-2">{t("How it works")}</h4>
          <ol className="list-decimal list-inside space-y-2 fc-muted">
            <li>{t("Clients submit message text to the API; use HTTPS/WSS outside local development")}</li>
            <li>{t("The server encrypts chat bodies for storage and decrypts them for authorized members")}</li>
            <li>{t("Server operators can access message content and encryption keys")}</li>
            <li>{t("Uploaded files are stored without application-level encryption")}</li>
          </ol>
        </div>
      </div>
    )
  },
  'authentication': {
    title: 'Authentication',
    content: () => (
      <div className="space-y-6">
        <p className="fc-muted">{t("JWT-based authentication with bcrypt password hashing.")}</p>
        <div className="fc-docs-code">
          <pre className="p-4 text-sm overflow-x-auto">
            <code className="fc-muted">{`// Register
POST /auth/register
{ "email": "...", "password": "...", "name": "..." }

// Login
POST /auth/login
{ "email": "...", "password": "..." }

// Returns: { "access_token": "jwt...", "token_type": "bearer", "user_id": "...", ... }`}</code>
          </pre>
        </div>
      </div>
    )
  },
  'rest-api': {
    title: 'REST API',
    content: () => (
      <div className="space-y-6">
        <p className="fc-muted">{t("Full REST API documentation.")}</p>
        <div className="space-y-4">
          <h4 className="font-semibold">{t("Authentication")}</h4>
          <div className="fc-docs-code p-4 font-mono text-sm">
            <div className="fc-accent">{t("POST")}</div>
            <div className="fc-muted">{t("POST /auth/register, POST /auth/login, GET /auth/me")}</div>
          </div>
          <h4 className="font-semibold">{t("Channels")}</h4>
          <div className="fc-docs-code p-4 font-mono text-sm">
            <div className="fc-accent">{t("GET")}</div>
            <div className="fc-muted">/channels</div>
            <div className="fc-accent mt-2">{t("POST")}</div>
            <div className="fc-muted">/channels</div>
          </div>
          <h4 className="font-semibold">{t("Messages")}</h4>
          <div className="fc-docs-code p-4 font-mono text-sm">
            <div className="fc-accent">{t("GET")}</div>
            <div className="fc-muted">/messages/channels/{t("{id}")}</div>
            <div className="fc-accent mt-2">{t("POST")}</div>
            <div className="fc-muted">/messages/channels/{t("{id}")}</div>
          </div>
        </div>
      </div>
    )
  },
  'websocket': {
    title: 'WebSocket Events',
    content: () => (
      <div className="space-y-6">
        <p className="fc-muted">{t("Real-time events via Socket.IO.")}</p>
        <div className="space-y-4">
          {[
            { event: 'message', desc: 'New message in channel' },
            { event: 'message_edited', desc: 'Message was edited' },
            { event: 'message_deleted', desc: 'Message was deleted' },
            { event: 'reaction', desc: 'Reaction added/removed' },
            { event: 'thread_reply', desc: 'New reply in thread' },
            { event: 'typing', desc: 'User started typing' },
            { event: 'stop_typing', desc: 'User stopped typing' },
            { event: 'mention', desc: 'You were mentioned' },
            { event: 'presence', desc: 'User online/offline status' },
          ].map((e, i) => (
            <div key={i} className="fc-docs-event">
              <code className="fc-accent font-mono text-sm">{e.event}</code>
              <span className="fc-muted text-sm">{e.desc}</span>
            </div>
          ))}
        </div>
      </div>
    )
  },
  'react-sdk': {
    title: 'React integration',
    content: () => (
      <div className="space-y-6">
        <p className="fc-muted">
          {t("The React components in this repository belong to the reference application. No public package with a provider or standalone component API is shipped here. Follow Quick Start for an HTTP integration, and WebSocket Events for realtime updates.")}</p>
        <p className="fc-muted">
          {t("The embeddable widget can be built with npm run build:widget and hosted as dist-widget/widget.iife.js. It requires a user token and a channel the user belongs to. Its receive path polls recent history; validate its limits before deployment.")}</p>
      </div>
    )
  },
}

export function Docs({ onBack }: DocsProps) {
  useI18n();
  const [expandedSections, setExpandedSections] = useState<string[]>(['getting-started'])
  const [activeItem, setActiveItem] = useState('introduction')

  const toggleSection = (id: string) => {
    setExpandedSections(prev => 
      prev.includes(id) ? prev.filter(s => s !== id) : [...prev, id]
    )
  }

  const activeContent = content[activeItem] || content['introduction']

  return (
    <div className="fc-docs">
      {/* Sidebar */}
      <aside className="fc-docs-sidebar">
        <div className="fc-docs-sidebar-section">
          <button
            onClick={onBack}
            className="fc-button"
          >
            <ArrowLeft className="w-4 h-4" />
            {t("Back to Home")}</button>
        </div>
        <div className="fc-docs-sidebar-section">
          <div className="fc-docs-identity"><Brand size="medium" /><span className="fc-kicker">{t("Documentation")}</span></div>
        </div>
        <nav className="fc-docs-navigation space-y-2">
          {sections.map(section => (
            <div key={section.id}>
              <button
                onClick={() => toggleSection(section.id)}
                className="fc-docs-section-button"
                aria-expanded={expandedSections.includes(section.id)}
              >
                {expandedSections.includes(section.id) ? (
                  <ChevronDown className="w-4 h-4" />
                ) : (
                  <ChevronRight className="w-4 h-4" />
                )}
                <section.icon className="w-4 h-4" />
                {t(section.title)}
              </button>
              {expandedSections.includes(section.id) && (
                <div className="ml-6 mt-1 space-y-1">
                  {section.items.map(item => (
                    <button
                      key={item.id}
                      onClick={() => setActiveItem(item.id)}
                      className={`fc-docs-item ${activeItem === item.id ? 'is-active' : ''}`}
                      aria-current={activeItem === item.id ? 'page' : undefined}
                    >
                      {t(item.title)}
                    </button>
                  ))}
                </div>
              )}
            </div>
          ))}
        </nav>
      </aside>

      {/* Main content */}
      <main className="fc-docs-content">
        <h1 className="text-3xl font-bold mb-8">{t(activeContent.title)}</h1>
        {activeContent.content()}
      </main>
    </div>
  )
}
