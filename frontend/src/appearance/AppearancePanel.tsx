import { t, useI18n } from '../i18n';
import { useEffect, useId, useRef, useState, type CSSProperties } from 'react';
import { createPortal } from 'react-dom';
import { ArrowUpRight, Check, Hash, Loader2, Moon, Palette, RotateCcw, SlidersHorizontal, Sun, Monitor, Type, X } from 'lucide-react';
import { Brand } from '../components/Brand';
import { FONT_OPTIONS, PRESETS, type AppearancePreferences } from '../lib/appearance';
import { useModalFocus } from '../lib/useModalFocus';

interface AppearancePanelProps {
  preferences: AppearancePreferences;
  resolvedMode: 'light' | 'dark';
  hasAccount: boolean;
  hasChanges: boolean;
  loading: boolean;
  saving: boolean;
  loadError: string;
  saveError: string;
  onChange: (changes: Partial<AppearancePreferences>) => void;
  onClose: () => void;
  onReset: () => void;
  onRetry: () => void;
  onSave: () => void;
}

function ColorControl({ label, value, onChange }: { label: string; value: string; onChange: (color: string) => void }) {
  useI18n();
  const id = useId();
  const [text, setText] = useState(value);
  useEffect(() => { setText(value); }, [value]);
  return (
    <div className="appearance-color-control">
      <label htmlFor={`${id}-text`}>{label}</label>
      <div className="appearance-color-input">
        <input type="color" value={value} onChange={(event) => onChange(event.target.value)} aria-label={t("Choose {value0}", { value0: String(label.toLowerCase()) })} />
        <input
          id={`${id}-text`}
          type="text"
          value={text}
          spellCheck={false}
          maxLength={7}
          aria-label={t("{value0}, hexadecimal color", { value0: String(label) })}
          onChange={(event) => {
            const next = event.target.value;
            setText(next);
            if (/^#?[0-9a-f]{6}$/i.test(next)) onChange(`#${next.replace(/^#/, '').toLowerCase()}`);
          }}
          onBlur={() => setText(value)}
        />
      </div>
    </div>
  );
}

const FONT_FAMILIES: Record<AppearancePreferences['font_family'], string> = {
  inter: '"Inter Variable", Inter, sans-serif',
  manrope: '"Manrope Variable", Manrope, sans-serif',
  serif: '"Source Serif 4 Variable", Georgia, serif',
  system: 'system-ui, sans-serif',
};

export function AppearancePanel({
  preferences, resolvedMode, hasAccount, hasChanges, loading, saving, loadError, saveError,
  onChange, onClose, onReset, onRetry, onSave,
}: AppearancePanelProps) {
  useI18n();
  const panelRef = useRef<HTMLElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const colorsRef = useRef<HTMLElement>(null);
  const typeRef = useRef<HTMLFieldSetElement>(null);
  const layoutRef = useRef<HTMLFieldSetElement>(null);
  const [activeSection, setActiveSection] = useState('colors');
  const titleId = useId();
  const sectionId = useId();

  useModalFocus(panelRef, onClose);

  const canvasColor = resolvedMode === 'dark' ? preferences.dark_background_color : preferences.background_color;
  const selectedFont = FONT_OPTIONS.find((font) => font.id === preferences.font_family)?.label;
  const stateLabel = saving ? 'Saving' : hasChanges ? 'Preview' : loading ? 'Loading' : loadError || !hasAccount ? 'Preview' : 'Saved';
  const stateDetail = saving ? 'Saving to your account…' : saveError ? 'Your changes haven’t been saved.' : hasChanges ? 'Previewing your changes' : loading ? 'Loading saved appearance…' : loadError ? 'Your saved settings are unavailable.' : hasAccount ? 'Saved to your account' : 'Sign in to keep this look';
  const sections = [
    { id: 'colors', label: t("Colors"), Icon: Palette, ref: colorsRef },
    { id: 'typography', label: t("Typography"), Icon: Type, ref: typeRef },
    { id: 'layout', label: t("Layout"), Icon: SlidersHorizontal, ref: layoutRef },
  ];
  const navigateTo = (id: string) => {
    const target = sections.find((section) => section.id === id)?.ref.current;
    const body = bodyRef.current;
    if (!body || !target) return;
    setActiveSection(id);
    target.focus({ preventScroll: true });
    body.scrollTo({ top: target.getBoundingClientRect().top - body.getBoundingClientRect().top + body.scrollTop - 4, behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
  };
  const trackSection = () => {
    const body = bodyRef.current;
    if (!body) return;
    const top = body.getBoundingClientRect().top + 25;
    let current = 'colors';
    for (const section of sections) if (section.ref.current && section.ref.current.getBoundingClientRect().top <= top) current = section.id;
    if (body.scrollTop > 0 && body.scrollHeight - body.scrollTop - body.clientHeight < 4) current = 'layout';
    setActiveSection(current);
  };
  const modes = [
    { id: 'light', label: t("Light"), Icon: Sun },
    { id: 'dark', label: t("Dark"), Icon: Moon },
    { id: 'system', label: t("System"), Icon: Monitor },
  ] as const;

  return createPortal(
    <div className="appearance-overlay" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section ref={panelRef} className="appearance-panel" role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1}>
        <header className="appearance-header">
          <div><div className="appearance-eyebrow"><span className="fc-kicker">{t("Your FreeCollab")}</span><span className={`appearance-state-chip${hasChanges ? ' is-preview' : ''}`} aria-hidden="true">{stateLabel === 'Saved' && <Check size={11} />}{t(stateLabel)}</span></div><h2 id={titleId}>{t("Make space your own.")}</h2><p>{t("A little more you. A little less noise.")}</p></div>
          <button data-autofocus type="button" className="fc-icon-button appearance-close" onClick={onClose} aria-label={t("Close appearance settings")}><X size={20} /></button>
        </header>

        <div className="appearance-preview-wrap">
          <figure className="appearance-preview" aria-label={t("Live appearance preview")}>
            <figcaption className="appearance-preview-heading"><Brand size="small" showName={false} /><span>{t("Live preview")}</span><span className="appearance-preview-font">{selectedFont && t(selectedFont)}</span><span className="appearance-preview-dot" /></figcaption>
            <h3 className="appearance-preview-title">{t("Room for good ideas.")}</h3>
            <div className="appearance-preview-message"><span className="appearance-preview-avatar">{t("A")}</span><div><strong>{t("Alex ")}<span>{t("just now")}</span></strong><p>{t("Good ideas need room to grow.")}</p></div></div>
            <div className="appearance-preview-reply"><span><Hash size={12} aria-hidden="true" /> {t(" studio-notes")}</span><span>{t("Let’s make something together.")}</span><ArrowUpRight size={14} aria-hidden="true" /></div>
          </figure>
          <p className="appearance-personal-note">{t("Only your view changes. Make it yours, then save.")}</p>
        </div>

        <nav className="appearance-navigation" aria-label={t("Appearance sections")}>
          {sections.map(({ id, label, Icon }) => <button key={id} type="button" aria-controls={`${sectionId}-${id}`} aria-current={activeSection === id ? 'location' : undefined} onClick={() => navigateTo(id)}><Icon size={15} aria-hidden="true" /><span>{label}</span></button>)}
        </nav>

        <div ref={bodyRef} className="appearance-body" onScroll={trackSection}>

          {(loading || loadError || saveError || !hasAccount) && (
            <div className={`appearance-status${loadError || saveError ? ' appearance-status-error' : ''}`} role={loadError || saveError ? 'alert' : 'status'}>
              {loading && <p><Loader2 size={15} className="appearance-spinner" /> {t(" Loading your saved appearance…")}</p>}
              {loadError && <><p>{t("We couldn’t load your saved appearance. You can still preview changes.")}</p><button type="button" className="appearance-text-button" onClick={onRetry} disabled={loading || saving}>{t("Try again")}</button></>}
              {saveError && <p>{t("Your changes couldn’t be saved. Your preview is still here. Please try again.")}</p>}
              {!hasAccount && <p>{t("Sign in to save your appearance preferences.")}</p>}
            </div>
          )}

          <section ref={colorsRef} id={`${sectionId}-colors`} className="appearance-section-group" aria-label={t("Color preferences")} tabIndex={-1}>
          <fieldset className="appearance-section" disabled={saving}>
            <legend><span>01</span> {t(" Light & shade")}</legend>
            <p className="appearance-section-note">{t("Set the mood, or follow your device.")}</p>
            <div className="appearance-segments">
              {modes.map(({ id, label, Icon }) => (
                <label className="appearance-choice" key={id}>
                  <input type="radio" name="appearance-mode" value={id} checked={preferences.mode === id} onChange={() => onChange({ mode: id })} />
                  <span><Icon size={17} />{label}</span>
                </label>
              ))}
            </div>
          </fieldset>

          <fieldset className="appearance-section" disabled={saving}>
            <legend><span>02</span> {t(" A considered palette")}</legend>
            <p className="appearance-section-note">{t("Start with a quiet pairing. Make it yours from there.")}</p>
            <div className="appearance-palettes">
              {PRESETS.map((preset) => {
                const selected = preferences.accent_color.toLowerCase() === preset.accent_color.toLowerCase()
                  && preferences.background_color.toLowerCase() === preset.background_color.toLowerCase()
                  && preferences.dark_background_color.toLowerCase() === preset.dark_background_color.toLowerCase();
                return (
                  <button
                    key={preset.id} type="button" className={`appearance-palette${selected ? ' is-selected' : ''}`}
                    aria-pressed={selected} title={t(preset.description)}
                    onClick={() => onChange({ accent_color: preset.accent_color, background_color: preset.background_color, dark_background_color: preset.dark_background_color })}
                  >
                    <span className="appearance-palette-swatch" style={{ '--palette-canvas': resolvedMode === 'dark' ? preset.dark_background_color : preset.background_color, '--palette-accent': preset.accent_color } as CSSProperties}><i />{selected && <Check size={13} />}</span>
                    <span>{t(preset.name)}</span>
                  </button>
                );
              })}
            </div>
            <div className="appearance-colors">
              <ColorControl label={t("Accent")} value={preferences.accent_color} onChange={(color) => onChange({ accent_color: color })} />
              <ColorControl label={`Canvas · ${resolvedMode}`} value={canvasColor} onChange={(color) => onChange(resolvedMode === 'dark' ? { dark_background_color: color } : { background_color: color })} />
            </div>
            <p className="appearance-color-note">{t("Choose your own colors. Text stays readable as you explore.")}</p>
          </fieldset>
          </section>

          <fieldset ref={typeRef} id={`${sectionId}-typography`} className="appearance-section" disabled={saving} tabIndex={-1}>
            <legend><span>03</span> {t(" The way words feel")}</legend>
            <p className="appearance-section-note">{t("Choose a typeface that feels easy to read.")}</p>
            <div className="appearance-fonts">
              {FONT_OPTIONS.map((font) => (
                <label className="appearance-font-choice" key={font.id}>
                  <input type="radio" name="appearance-font" value={font.id} checked={preferences.font_family === font.id} onChange={() => onChange({ font_family: font.id })} />
                  <span><span className="appearance-font-sample" style={{ fontFamily: FONT_FAMILIES[font.id] }}>{t("Aa")}</span><span><strong>{t(font.label)}</strong><small>{t(font.description)}</small></span><Check size={16} className="appearance-font-check" /></span>
                </label>
              ))}
            </div>
            <div className="appearance-subsetting"><h3>{t("Text size")}</h3><div className="appearance-segments">
              {(['small', 'medium', 'large'] as const).map((size) => <label className="appearance-choice" key={size}><input type="radio" name="appearance-size" checked={preferences.font_size === size} onChange={() => onChange({ font_size: size })} /><span className={`appearance-size-${size}`}>{size === 'medium' ? t("Default") : size === 'small' ? t("Small") : t("Large")}</span></label>)}
            </div></div>
          </fieldset>

          <fieldset ref={layoutRef} id={`${sectionId}-layout`} className="appearance-section appearance-section-last" disabled={saving} tabIndex={-1}>
            <legend><span>04</span> {t(" Room for the details")}</legend>
            <p className="appearance-section-note">{t("A little breathing room, or a closer view.")}</p>
            <div className="appearance-subsetting"><h3>{t("Spacing")}</h3><div className="appearance-segments">
              {(['comfortable', 'compact'] as const).map((density) => <label className="appearance-choice" key={density}><input type="radio" name="appearance-density" checked={preferences.density === density} onChange={() => onChange({ density })} /><span>{density === 'comfortable' ? t("Comfortable") : t("Compact")}</span></label>)}
            </div></div>
            <div className="appearance-subsetting"><h3>{t("Corners")}</h3><div className="appearance-segments">
              {(['soft', 'rounded', 'square'] as const).map((corner) => <label className="appearance-choice" key={corner}><input type="radio" name="appearance-corners" checked={preferences.corner_style === corner} onChange={() => onChange({ corner_style: corner })} /><span><i className={`appearance-corner-sample appearance-corner-${corner}`} />{t(corner === 'soft' ? 'Soft' : corner === 'rounded' ? 'Rounded' : 'Square')}</span></label>)}
            </div></div>
          </fieldset>
        </div>

        <footer className="appearance-footer">
          <p className="appearance-footer-state" role="status"><span className={`appearance-footer-dot${hasChanges || saveError ? ' is-preview' : ''}`} /><span>{t(stateDetail)}{hasChanges && !saveError && <small>{t("Save to keep this look.")}</small>}</span></p>
          <div className="appearance-footer-actions">
            <button type="button" className="appearance-reset" onClick={onReset} disabled={saving}><RotateCcw size={15} /><span>{t("Reset")}</span></button>
            <div><button type="button" className="fc-button" onClick={onClose}>{t("Cancel")}</button><button type="button" className="fc-button fc-button-primary" onClick={onSave} disabled={!hasAccount || !hasChanges || loading || saving}>{saving && <Loader2 size={15} className="appearance-spinner" />}{saving ? t("Saving…") : t("Save changes")}</button></div>
          </div>
        </footer>
      </section>
    </div>,
    document.body,
  );
}
