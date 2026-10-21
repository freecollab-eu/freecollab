import { t, useI18n } from '../i18n';
interface BrandProps {
  size?: 'small' | 'medium' | 'large'
  showName?: boolean
  className?: string
}

/** A shared four-leaf mark: separate pieces meeting around a common space. */
export function Brand({ size = 'medium', showName = true, className = '' }: BrandProps) {
  useI18n();
  return (
    <span className={`fc-brand fc-brand--${size} ${className}`} role={showName ? undefined : 'img'} aria-label={showName ? undefined : t("FreeCollab")}>
      <svg className="fc-brand-mark" viewBox="0 0 32 32" fill="currentColor" aria-hidden="true">
        {[0, 90, 180, 270].map(rotation => (
          <path key={rotation} transform={`rotate(${rotation} 16 16)`} d="M3 3h7a5 5 0 0 1 5 5v7H8a5 5 0 0 1-5-5V3Z" />
        ))}
      </svg>
      {showName && <span className="fc-brand-name">{t("FreeCollab")}<span className="fc-brand-period">.</span></span>}
    </span>
  )
}
