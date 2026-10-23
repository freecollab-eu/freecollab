import { t, useI18n } from '../i18n';
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Check, X } from 'lucide-react';
import { api } from '../lib/api';
import {
  applyAppearance,
  DEFAULT_APPEARANCE,
  normalizeAppearance,
  type AppearancePreferences,
} from '../lib/appearance';
import { AppearancePanel } from './AppearancePanel';
import '../styles/appearance.css';

const CACHE_PREFIX = 'freecollab.appearance:';

function readCache(userId: string | null): AppearancePreferences {
  if (!userId || api.isMemorySession()) return { ...DEFAULT_APPEARANCE };
  try {
    const cached = localStorage.getItem(`${CACHE_PREFIX}${userId}`);
    return cached ? normalizeAppearance(JSON.parse(cached)) : { ...DEFAULT_APPEARANCE };
  } catch {
    return { ...DEFAULT_APPEARANCE };
  }
}

function writeCache(userId: string, preferences: AppearancePreferences) {
  if (api.isMemorySession()) return;
  try {
    localStorage.setItem(`${CACHE_PREFIX}${userId}`, JSON.stringify(preferences));
  } catch {
    // Storage can be unavailable. The authenticated API remains authoritative.
  }
}

function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback;
}

interface AppearanceContextValue {
  openAppearance: () => void;
  isAppearanceOpen: boolean;
}

const AppearanceContext = createContext<AppearanceContextValue | null>(null);

export function useAppearance() {
  const context = useContext(AppearanceContext);
  if (!context) throw new Error(t("useAppearance must be used inside AppearanceProvider"));
  return context;
}

export function AppearanceProvider({ children }: { children: ReactNode }) {
  useI18n();
  const initialAccount = useRef(api.getCurrentUserId());
  const [accountId, setAccountId] = useState(initialAccount.current);
  const [saved, setSaved] = useState(() => readCache(initialAccount.current));
  const [draft, setDraft] = useState(saved);
  const [isOpen, setIsOpen] = useState(false);
  const [loading, setLoading] = useState(!!initialAccount.current);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [saveError, setSaveError] = useState('');
  const [savedNotice, setSavedNotice] = useState<string | null>(null);
  const [prefersDark, setPrefersDark] = useState(() => window.matchMedia('(prefers-color-scheme: dark)').matches);
  const accountRef = useRef(accountId);
  const savedRef = useRef(saved);
  const draftRef = useRef(draft);
  const openRef = useRef(false);
  const dirtyRef = useRef(false);
  const accountGeneration = useRef(0);
  const loadSequence = useRef(0);
  const saveSequence = useRef(0);
  const panelSession = useRef(0);
  const loadAbort = useRef<AbortController | null>(null);
  const saveAbort = useRef<AbortController | null>(null);

  const showPreferences = useCallback((preferences: AppearancePreferences) => {
    draftRef.current = preferences;
    setDraft(preferences);
    applyAppearance(preferences);
  }, []);

  const loadPreferences = useCallback(async () => {
    const userId = accountRef.current;
    if (!userId) return;
    const generation = accountGeneration.current;
    const sequence = ++loadSequence.current;
    loadAbort.current?.abort();
    const controller = new AbortController();
    loadAbort.current = controller;
    setLoading(true);
    setLoadError('');
    try {
      const response = await api.request<AppearancePreferences>('/preferences/appearance', { signal: controller.signal });
      if (controller.signal.aborted || generation !== accountGeneration.current || sequence !== loadSequence.current || userId !== accountRef.current) return;
      const preferences = normalizeAppearance(response);
      savedRef.current = preferences;
      setSaved(preferences);
      writeCache(userId, preferences);
      if (!openRef.current || !dirtyRef.current) showPreferences(preferences);
    } catch (error) {
      if (controller.signal.aborted || generation !== accountGeneration.current || sequence !== loadSequence.current) return;
      setLoadError(errorMessage(error, 'Your saved appearance could not be loaded. Please try again.'));
    } finally {
      if (generation === accountGeneration.current && sequence === loadSequence.current) setLoading(false);
    }
  }, [showPreferences]);

  const closeAppearance = useCallback(() => {
    openRef.current = false;
    dirtyRef.current = false;
    setIsOpen(false);
    setSaveError('');
    showPreferences(savedRef.current);
  }, [showPreferences]);

  const openAppearance = useCallback(() => {
    panelSession.current += 1;
    openRef.current = true;
    dirtyRef.current = false;
    setSaveError('');
    setSavedNotice(null);
    showPreferences(savedRef.current);
    setIsOpen(true);
  }, [showPreferences]);

  const updateDraft = useCallback((changes: Partial<AppearancePreferences>) => {
    const preferences = normalizeAppearance({ ...draftRef.current, ...changes });
    dirtyRef.current = JSON.stringify(preferences) !== JSON.stringify(savedRef.current);
    setSaveError('');
    showPreferences(preferences);
  }, [showPreferences]);

  const resetDraft = useCallback(() => {
    const preferences = { ...DEFAULT_APPEARANCE };
    dirtyRef.current = JSON.stringify(preferences) !== JSON.stringify(savedRef.current);
    setSaveError('');
    showPreferences(preferences);
  }, [showPreferences]);

  const savePreferences = useCallback(async () => {
    const userId = accountRef.current;
    if (!userId || saveAbort.current || !dirtyRef.current) return;
    const preferences = { ...draftRef.current };
    const generation = accountGeneration.current;
    const session = panelSession.current;
    const sequence = ++saveSequence.current;
    // A GET started before this explicit save must never replace its result.
    loadSequence.current += 1;
    loadAbort.current?.abort();
    setLoading(false);
    const controller = new AbortController();
    saveAbort.current = controller;
    setSaving(true);
    setSaveError('');
    try {
      const response = await api.request<AppearancePreferences>('/preferences/appearance', {
        method: 'PUT',
        body: JSON.stringify(preferences),
        signal: controller.signal,
      });
      if (controller.signal.aborted || generation !== accountGeneration.current || sequence !== saveSequence.current || userId !== accountRef.current) return;
      const persisted = normalizeAppearance(response);
      savedRef.current = persisted;
      setSaved(persisted);
      writeCache(userId, persisted);
      setLoadError('');
      setSavedNotice(userId);
      if (session === panelSession.current) {
        dirtyRef.current = false;
        showPreferences(persisted);
        openRef.current = false;
        setIsOpen(false);
      } else if (!openRef.current || !dirtyRef.current) {
        dirtyRef.current = false;
        showPreferences(persisted);
      } else {
        dirtyRef.current = JSON.stringify(draftRef.current) !== JSON.stringify(persisted);
      }
    } catch (error) {
      if (controller.signal.aborted || generation !== accountGeneration.current || sequence !== saveSequence.current) return;
      setSaveError(errorMessage(error, 'Your changes could not be saved. Your preview is still here; please try again.'));
    } finally {
      if (generation === accountGeneration.current && sequence === saveSequence.current) {
        saveAbort.current = null;
        setSaving(false);
      }
    }
  }, [showPreferences]);

  useEffect(() => {
    const switchAccount = () => {
      const userId = api.getCurrentUserId();
      if (userId === accountRef.current) return;
      accountGeneration.current += 1;
      loadSequence.current += 1;
      saveSequence.current += 1;
      loadAbort.current?.abort();
      saveAbort.current?.abort();
      saveAbort.current = null;
      accountRef.current = userId;
      setAccountId(userId);
      const preferences = readCache(userId);
      savedRef.current = preferences;
      setSaved(preferences);
      dirtyRef.current = false;
      openRef.current = false;
      panelSession.current += 1;
      setIsOpen(false);
      setSaving(false);
      setLoading(!!userId);
      setLoadError('');
      setSaveError('');
      setSavedNotice(null);
      // Clear the previous account's preview before requesting the next one.
      showPreferences(preferences);
      if (userId) void loadPreferences();
    };
    window.addEventListener('freecollab:auth-changed', switchAccount);
    applyAppearance(savedRef.current);
    void loadPreferences();
    return () => {
      window.removeEventListener('freecollab:auth-changed', switchAccount);
      loadAbort.current?.abort();
      saveAbort.current?.abort();
    };
  }, [loadPreferences, showPreferences]);

  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const updateSystemMode = () => {
      setPrefersDark(media.matches);
      applyAppearance(openRef.current ? draftRef.current : savedRef.current);
    };
    media.addEventListener('change', updateSystemMode);
    return () => media.removeEventListener('change', updateSystemMode);
  }, []);

  useEffect(() => {
    const syncAppearance = (event: StorageEvent) => {
      const userId = accountRef.current;
      if (!userId || event.key !== `${CACHE_PREFIX}${userId}`) return;
      const preferences = readCache(userId);
      savedRef.current = preferences;
      setSaved(preferences);
      if (!openRef.current || !dirtyRef.current) showPreferences(preferences);
    };
    window.addEventListener('storage', syncAppearance);
    return () => window.removeEventListener('storage', syncAppearance);
  }, [showPreferences]);

  useEffect(() => {
    if (!savedNotice) return;
    const timer = window.setTimeout(() => setSavedNotice(null), 6000);
    return () => window.clearTimeout(timer);
  }, [savedNotice]);

  const resolvedMode = draft.mode === 'system' ? (prefersDark ? 'dark' : 'light') : draft.mode;
  const hasChanges = JSON.stringify(draft) !== JSON.stringify(saved);
  const contextValue = useMemo(() => ({ openAppearance, isAppearanceOpen: isOpen }), [openAppearance, isOpen]);

  return (
    <AppearanceContext.Provider value={contextValue}>
      {children}
      {savedNotice && savedNotice === accountId && !isOpen && (
        <div className="appearance-save-notice" role="status">
          <Check size={17} aria-hidden="true" />
          <span>{t("Appearance saved to your account.")}</span>
          <button type="button" className="fc-icon-button" aria-label={t("Dismiss appearance confirmation")} onClick={() => setSavedNotice(null)}><X size={16} /></button>
        </div>
      )}
      {isOpen && (
        <AppearancePanel
          preferences={draft}
          resolvedMode={resolvedMode}
          hasAccount={!!accountId}
          hasChanges={hasChanges}
          loading={loading}
          saving={saving}
          loadError={loadError}
          saveError={saveError}
          onChange={updateDraft}
          onClose={closeAppearance}
          onReset={resetDraft}
          onRetry={() => { void loadPreferences(); }}
          onSave={() => { void savePreferences(); }}
        />
      )}
    </AppearanceContext.Provider>
  );
}
