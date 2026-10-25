import { t, useI18n } from './i18n';
import { useState, useEffect, useRef } from 'react'
import { Landing } from './components/Landing'
import { Demo } from './components/Demo'
import { Auth } from './components/Auth'
import { MeetingGuest } from './components/MeetingGuest'
import { Chat } from './components/Chat'
import { Admin } from './components/Admin'
import { Docs } from './components/Docs'
import { AcceptInvite } from './components/AcceptInvite'
import { AuthenticationFlow } from './components/AuthenticationFlow'
import { WorkspaceInvitation } from './components/WorkspaceInvitation'
import { SSOCallback } from './components/SSOCallback'
import { useAccount } from './account/AccountProvider'
import { api } from './lib/api'
import { disconnectSocket } from './lib/socket'
import { type MFAChallenge, needsSecondFactor } from './lib/account-api'
import type { SSOExchange } from './lib/integrations-api'
import { demoDestinationHash, readDemoDestination, type DemoDestination } from './lib/demo-showroom'
import { nativeSourceFromLocation, pendingNativeSource, rememberNativeSource } from './lib/native-links'

type View = 'landing' | 'demo' | 'auth' | 'chat' | 'admin' | 'docs' | 'invite' | 'account-link' | 'workspace-invite' | 'sso' | 'meeting'
const INVITE_KEY = 'freecollab_pending_invite'
const WORKFORCE_INVITE_KEY = 'freecollab_pending_workforce_invite'

function readWorkforceInvite(): { login_id: string; token: string } | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(WORKFORCE_INVITE_KEY) || 'null')
    return value && typeof value.login_id === 'string' && /^[A-Za-z0-9_-]{16,200}$/.test(value.login_id) && typeof value.token === 'string' && value.token.length <= 512 ? value : null
  } catch { return null }
}

function readInvite(): string | null {
  const fromHash = new URLSearchParams(window.location.hash.slice(1)).get('invite')
  if (fromHash && fromHash.length <= 512) sessionStorage.setItem(INVITE_KEY, fromHash)
  return sessionStorage.getItem(INVITE_KEY)
}

export default function App() {
  useI18n();
  const { refreshWorkspaces } = useAccount()
  const [meetingRoute, setMeetingRoute] = useState<{ id: string; invitationToken?: string } | null>(null)
  const [accountLink, setAccountLink] = useState<{ kind: 'verify-email' | 'reset-password'; token: string } | null>(null)
  const [workspaceInvite, setWorkspaceInvite] = useState<string | null>(null)
  const [view, setView] = useState<View>('landing')
  const [demoDestination, setDemoDestination] = useState<DemoDestination>(() => readDemoDestination(window.location.hash) ?? {})
  const [invite, setInvite] = useState<string | null>(() => readInvite())
  const [initialChannelId, setInitialChannelId] = useState<string | null>(null)
  const [notice, setNotice] = useState('')
  const [ssoCode, setSSOCode] = useState<string | null>(null)
  const [ssoChallenge, setSSOChallenge] = useState<MFAChallenge | null>(null)
  const [organizationLoginId, setOrganizationLoginId] = useState<string | null>(null)
  const [organizationInvitationToken, setOrganizationInvitationToken] = useState<string | null>(null)
  const [syncReturn, setSyncReturn] = useState<{ workspaceId: string; connectionId: string } | null>(null)
  const organizationCompletion = useRef(false)
  const pendingSSO = useRef<string | null>(null)
  const initialRouteRead = useRef(false)
  const [accountId, setAccountId] = useState(() => api.getCurrentUserId())
  const [sessionRevision, setSessionRevision] = useState(0)

  useEffect(() => {
    const route = () => {
      try {
        const source = nativeSourceFromLocation()
        if (source) {
          const previous = pendingNativeSource()
          if (!previous || JSON.stringify(previous.source) !== JSON.stringify(source)) rememberNativeSource(source)
          setView(api.isAuthenticated() ? 'chat' : 'auth'); return
        }
      } catch (failure) {
        setNotice(failure instanceof Error ? t(failure.message) : t('This app link could not be opened.'))
        setView(api.isAuthenticated() ? 'chat' : 'auth'); return
      }
      const params = new URLSearchParams(window.location.hash.slice(1))
      const demo = readDemoDestination(window.location.hash)
      if (demo) { setDemoDestination(demo); setView('demo'); return }
      const meetingId = params.get('meeting')
      if (meetingId && /^[A-Za-z0-9_-]{1,128}$/.test(meetingId)) {
        const invitationToken = params.get('meeting_invite')
        if (invitationToken) window.history.replaceState(null, '', window.location.pathname + window.location.search + '#meeting=' + encodeURIComponent(meetingId))
        setMeetingRoute(previous => ({ id: meetingId, invitationToken: invitationToken && invitationToken.length <= 512 ? invitationToken : previous?.id === meetingId ? previous.invitationToken : undefined }))
        setView('meeting'); return
      }
      setMeetingRoute(null)
      const syncConnected = params.get('sync_connected')
      const providerConnected = params.get('provider_connected')
      const providerError = params.get('provider_error')
      if (providerConnected || providerError) {
        window.history.replaceState(null, '', window.location.pathname + window.location.search)
        setNotice(providerError ? t('Provider consent could not be completed. Open Provider accounts and try again.') : t('Provider account connected. Your delegated access is available in Provider accounts.'))
        setView(api.isAuthenticated() ? 'chat' : 'auth'); return
      }
      const syncError = params.get('sync_error')
      if (syncConnected || syncError) {
        window.history.replaceState(null, '', window.location.pathname + window.location.search)
        const workspaceId = params.get('workspace_id')
        if (!syncError && syncConnected && /^[A-Za-z0-9_-]{1,128}$/.test(syncConnected) && workspaceId && /^[A-Za-z0-9_-]{1,128}$/.test(workspaceId) && api.isAuthenticated()) {
          setSyncReturn({ workspaceId, connectionId: syncConnected })
          const token = api.getAccessToken()
          void api.getMe().then(me => { if (token === api.getAccessToken() && me.session_scope !== 'workspace') return refreshWorkspaces(workspaceId) }).catch(() => {})
          setNotice(t("Provider account connected. Test the saved connection, then enable synchronization in Integrations → Sync."))
        } else setNotice(t("The provider connection could not be completed. Open Integrations → Sync and start again."))
        setView(api.isAuthenticated() ? 'chat' : 'auth'); return
      }
      const handoff = params.get('sso')
      const ssoError = params.get('sso_error')
      if (handoff || ssoError) {
        // Consume the one-time handoff from memory; keep credentials out of URLs,
        // browser history and application storage before any further requests.
        window.history.replaceState(null, '', window.location.pathname + window.location.search)
        if (handoff && handoff.length <= 512) { pendingSSO.current = handoff; setSSOCode(handoff); setSSOChallenge(null); setView('sso'); return }
        pendingSSO.current = null
        const errors: Record<string, string> = {
          account_link_required: 'An account already uses this email. Sign in with your password, verify your email, then link single sign-on in Account settings.',
          workspace_link_required: 'Sign in with your FreeCollab password, accept your workspace invitation and verify your email, then link your organization identity in Integrations → Sign-in.',
          identity_already_linked: 'This organization identity is already linked to another FreeCollab account. Use that account or contact your workspace administrator.',
          workforce_identity_conflict: 'Your workspace has a managed identity for this sign-in. Ask its administrator to resolve the account conflict; your personal account stays available with your FreeCollab password.',
          workforce_access_required: 'Your organization must provision or invite this identity. Ask your workspace administrator for access. No separate FreeCollab account or password is needed.',
          email_not_verified: 'Your identity provider must verify your email before you can create a FreeCollab account.',
          provider_denied: 'Single sign-on was cancelled or declined by your identity provider.',
        }
        setNotice(errors[ssoError || ''] || t("Single sign-on could not be completed. Please start again."))
        const workforce = readWorkforceInvite()
        if (workforce) { setOrganizationLoginId(workforce.login_id); setOrganizationInvitationToken(workforce.token) }
        setView(api.isAuthenticated() ? 'chat' : 'auth'); return
      }
      // StrictMode replays initial effects after the sensitive fragment has
      // already been removed. Preserve its in-memory handoff through that replay.
      if (pendingSSO.current && !window.location.hash) { setSSOCode(pendingSSO.current); setView('sso'); return }
      pendingSSO.current = null
      const organization = params.get('workspace-sso')
      if (organization && /^[A-Za-z0-9_-]{16,200}$/.test(organization)) {
        const incoming = params.get('workspace-invite')
        if (incoming && incoming.length <= 512) { sessionStorage.setItem(WORKFORCE_INVITE_KEY, JSON.stringify({ login_id: organization, token: incoming })); window.history.replaceState(null, '', window.location.pathname + window.location.search + '#workspace-sso=' + encodeURIComponent(organization)) }
        const workforce = readWorkforceInvite()
        setOrganizationInvitationToken(workforce?.login_id === organization ? workforce.token : null)
        setOrganizationLoginId(organization); setWorkspaceInvite(null); setSSOChallenge(null); organizationCompletion.current = false; setView('auth'); return
      }
      for (const kind of ['verify-email', 'reset-password'] as const) {
        const value = params.get(kind)
        if (value && value.length <= 512) { setAccountLink({kind, token:value}); setView('account-link'); return }
      }
      setAccountLink(null)
      const incomingWorkspaceInvite = params.get('workspace-invite')
      if (incomingWorkspaceInvite && incomingWorkspaceInvite.length <= 512) sessionStorage.setItem('freecollab_pending_workspace_invite', incomingWorkspaceInvite)
      const workspaceToken = sessionStorage.getItem('freecollab_pending_workspace_invite')
      setWorkspaceInvite(workspaceToken)
      if (workspaceToken) { setView(api.isAuthenticated() ? 'workspace-invite' : 'auth'); return }
      const token = readInvite()
      setInvite(token)
      setView(token ? (api.isAuthenticated() ? 'invite' : 'auth') : api.isAuthenticated() ? 'chat' : 'landing')
    }
    const expired = () => {
      pendingSSO.current = null; setSSOCode(null)
      setOrganizationLoginId(null); setOrganizationInvitationToken(null); setSSOChallenge(null); organizationCompletion.current = false
      api.logout(); disconnectSocket()
      setNotice(t("Your session has expired. Sign in again to continue."))
      setView('auth')
    }
    const signedOut = (event: Event) => {
      pendingSSO.current = null; setSSOCode(null)
      setOrganizationLoginId(null); setOrganizationInvitationToken(null); setSSOChallenge(null); organizationCompletion.current = false
      window.history.replaceState(null, '', window.location.pathname + window.location.search)
      api.logout(); disconnectSocket()
      setNotice((event as CustomEvent<{notice?:string}>).detail?.notice || t("Signed out. Sign in to continue."))
      setView('auth')
    }
    window.addEventListener('freecollab:signed-out', signedOut)
    const accountChanged = () => { setAccountId(api.getCurrentUserId()); setInitialChannelId(null); setSyncReturn(null); setSessionRevision(value => value + 1) }
    if (!initialRouteRead.current) { initialRouteRead.current = true; route() }
    window.addEventListener('hashchange', route)
    window.addEventListener('freecollab:session-expired', expired)
    window.addEventListener('freecollab:auth-changed', accountChanged)
    const storageChanged = (event: StorageEvent) => {
      if (event.key === null || ['freecollab_token', 'freecollab_user_id'].includes(event.key)) {
        disconnectSocket()
        api.syncStoredSession()
        window.dispatchEvent(new Event('freecollab:auth-changed'))
        route()
      }
    }
    window.addEventListener('storage', storageChanged)
    return () => { window.removeEventListener('hashchange', route); window.removeEventListener('freecollab:session-expired', expired); window.removeEventListener('freecollab:auth-changed', accountChanged); window.removeEventListener('storage', storageChanged); window.removeEventListener('freecollab:signed-out', signedOut) }
  }, [])

  const handleTryDemo = () => setView(api.isAuthenticated() ? 'chat' : 'auth')
  const handleExploreDemo = (destination: DemoDestination = {}) => { window.history.pushState(null, '', window.location.pathname + window.location.search + demoDestinationHash(destination)); setDemoDestination(destination); setView('demo'); window.scrollTo(0, 0) }
  const leaveDemo = () => { window.history.replaceState(null, '', window.location.pathname + window.location.search); setView('landing'); window.scrollTo(0, 0) }

  const handleAuthSuccess = () => {
    const customerSignIn = organizationCompletion.current
    organizationCompletion.current = false
    if (customerSignIn) { sessionStorage.removeItem(WORKFORCE_INVITE_KEY); sessionStorage.removeItem('freecollab_pending_workspace_invite'); setWorkspaceInvite(null); setOrganizationInvitationToken(null) }
    setNotice(''); setSSOChallenge(null); setOrganizationLoginId(null)
    window.history.replaceState(null, '', window.location.pathname + window.location.search)
    setView(!customerSignIn && workspaceInvite ? 'workspace-invite' : invite ? 'invite' : 'chat')
    const owner = api.getAccessToken()
    void api.getMe().then(me => { if (owner === api.getAccessToken()) return refreshWorkspaces(me.session_scope === 'workspace' ? me.workspace_id || undefined : undefined) }).catch(() => {})
  }
  const handleSSOComplete = (result: SSOExchange) => {
    pendingSSO.current = null
    setSSOCode(null)
    if ('linked' in result) {
      if (result.user_id !== api.getCurrentUserId()) { setNotice(t("The signed-in account changed. Sign in again to check your linked identity.")); setView('auth'); return }
      setNotice(t("Single sign-on is linked to your FreeCollab account.")); if (result.workspace_id) void refreshWorkspaces(result.workspace_id); setView('chat'); return
    }
    organizationCompletion.current = !!result.workspace_id
    if (needsSecondFactor(result)) { setSSOChallenge(result); setView('auth'); return }
    api.applySession(result); handleAuthSuccess()
  }
  const handleLogout = (warning?: string) => {
    api.logout()
    disconnectSocket()
    setNotice(warning || '')
    setOrganizationLoginId(null); setOrganizationInvitationToken(null); setSyncReturn(null); setSSOChallenge(null); organizationCompletion.current = false
    window.history.replaceState(null, '', window.location.pathname + window.location.search)
    setView('landing')
  }
  const clearInvite = () => {
    sessionStorage.removeItem(INVITE_KEY); setInvite(null)
    window.history.replaceState(null, '', window.location.pathname + window.location.search)
  }
  const clearAccountLink = () => { setAccountLink(null); window.history.replaceState(null, '', window.location.pathname + window.location.search) }
  const clearWorkspaceInvite = () => { sessionStorage.removeItem('freecollab_pending_workspace_invite'); setWorkspaceInvite(null); window.history.replaceState(null, '', window.location.pathname + window.location.search) }
  const handleAdmin = () => setView('admin')
  const handleBackFromAdmin = () => setView('chat')
  const handleDocs = () => setView('docs')
  const handleBackFromDocs = () => setView('landing')

  switch (view) {
    case 'demo':
      return <Demo destination={demoDestination} onBack={leaveDemo} onOpenApp={() => { window.history.replaceState(null, '', window.location.pathname + window.location.search); handleTryDemo() }} />
    case 'meeting':
      return meetingRoute ? <MeetingGuest key={meetingRoute.id} meetingId={meetingRoute.id} invitationToken={meetingRoute.invitationToken} onLeave={() => { setMeetingRoute(null); window.history.replaceState(null, '', window.location.pathname + window.location.search); setView(api.isAuthenticated() ? 'chat' : 'landing') }} /> : null
    case 'sso':
      return ssoCode ? <SSOCallback key={ssoCode} code={ssoCode} onComplete={handleSSOComplete} onBack={() => { pendingSSO.current = null; setSSOCode(null); setView(api.isAuthenticated() ? 'chat' : 'auth') }} /> : null
    case 'account-link':
      return accountLink ? <AuthenticationFlow key={accountLink.token} {...accountLink} onSuccess={() => { clearAccountLink(); setView(api.isAuthenticated() ? 'chat' : 'auth') }} onBack={() => { clearAccountLink(); setView(api.isAuthenticated() ? 'chat' : 'landing') }} /> : null
    case 'workspace-invite':
      return workspaceInvite ? <WorkspaceInvitation key={`${accountId}:${workspaceInvite}`} token={workspaceInvite} onJoined={async (workspace) => { const owner = api.getAccessToken(); await refreshWorkspaces(workspace.id); if (owner !== api.getAccessToken()) return; clearWorkspaceInvite(); setView('chat') }} onBack={() => { clearWorkspaceInvite(); setView('chat') }} /> : null
    case 'auth':
      return <Auth onSuccess={handleAuthSuccess} invited={!!invite || !!workspaceInvite} notice={notice} initialChallenge={ssoChallenge} organizationLoginId={organizationLoginId} organizationInvitationToken={organizationInvitationToken} onOrganizationChange={id => { setOrganizationLoginId(id); setOrganizationInvitationToken(null); organizationCompletion.current = false; setNotice(''); window.history.replaceState(null, '', window.location.pathname + window.location.search + (id ? '#workspace-sso=' + encodeURIComponent(id) : '')) }} onBack={() => { clearInvite(); clearWorkspaceInvite(); setSSOChallenge(null); setOrganizationLoginId(null); setOrganizationInvitationToken(null); organizationCompletion.current = false; setView('landing') }} />
    case 'invite':
      return invite ? <AcceptInvite key={accountId} token={invite} onJoined={async (channel) => { const owner = api.getAccessToken(); await refreshWorkspaces(channel.workspace_id); if (owner !== api.getAccessToken()) return; clearInvite(); setInitialChannelId(channel.id); setView('chat') }} onCancel={() => { clearInvite(); setView('chat') }} /> : null
    case 'chat':
      return <><Chat key={`${accountId}:${sessionRevision}`} onLogout={handleLogout} onAdmin={handleAdmin} initialChannelId={initialChannelId} initialSyncReturn={syncReturn} />{notice && <p role="status" className="fc-alert fc-toast">{notice}<button type="button" className="fc-icon-button" aria-label={t("Dismiss account notice")} onClick={() => setNotice('')}>×</button></p>}</>
    case 'admin':
      return <Admin key={accountId} onBack={handleBackFromAdmin} />
    case 'docs':
      return <Docs onBack={handleBackFromDocs} />
    default:
      return <><Landing onTryDemo={handleTryDemo} onExploreDemo={handleExploreDemo} onDocs={handleDocs} />{notice && <p role="status" className="fc-alert fc-toast">{notice}</p>}</>
  }
}
