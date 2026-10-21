export interface AppearancePreferences {
  version: 1
  mode: 'light' | 'dark' | 'system'
  accent_color: string
  background_color: string
  dark_background_color: string
  font_family: 'inter' | 'manrope' | 'serif' | 'system'
  font_size: 'small' | 'medium' | 'large'
  density: 'comfortable' | 'compact'
  corner_style: 'soft' | 'rounded' | 'square'
}

export const DEFAULT_APPEARANCE: AppearancePreferences = {
  version: 1, mode: 'light', accent_color: '#36594c', background_color: '#f6f5f1',
  dark_background_color: '#151b18', font_family: 'inter', font_size: 'medium',
  density: 'comfortable', corner_style: 'soft',
}

export const PRESETS = [
  { id: 'linen', name: 'Linen', description: 'Warm linen & forest', accent_color: '#36594c', background_color: '#f6f5f1', dark_background_color: '#151b18' },
  { id: 'fjord', name: 'Fjord', description: 'Cool stone & ocean', accent_color: '#365f79', background_color: '#f1f4f5', dark_background_color: '#141c22' },
  { id: 'clay', name: 'Clay', description: 'Soft sand & terracotta', accent_color: '#955a43', background_color: '#f8f3ed', dark_background_color: '#231b17' },
  { id: 'ink', name: 'Ink', description: 'Paper & graphite', accent_color: '#454951', background_color: '#f4f4f2', dark_background_color: '#191a1d' },
] as const

export const FONT_OPTIONS = [
  { id: 'inter', label: 'Inter', description: 'Clear & considered' },
  { id: 'manrope', label: 'Manrope', description: 'Soft & geometric' },
  { id: 'serif', label: 'Source Serif', description: 'Warm & editorial' },
  { id: 'system', label: 'System', description: 'Familiar to your device' },
] as const

const hex = /^#[0-9a-f]{6}$/i
const choices = {
  mode: ['light', 'dark', 'system'], font_family: ['inter', 'manrope', 'serif', 'system'],
  font_size: ['small', 'medium', 'large'], density: ['comfortable', 'compact'],
  corner_style: ['soft', 'rounded', 'square'],
} as const

/** Accept only the versioned, finite preference vocabulary; never arbitrary CSS. */
export function normalizeAppearance(value: unknown): AppearancePreferences {
  const result = { ...DEFAULT_APPEARANCE }
  if (!value || typeof value !== 'object' || Array.isArray(value)) return result
  const input = value as Record<string, unknown>
  if (input.version !== undefined && input.version !== 1) return result
  for (const key of ['accent_color', 'background_color', 'dark_background_color'] as const) {
    if (typeof input[key] === 'string' && hex.test(input[key])) result[key] = input[key].toLowerCase()
  }
  for (const key of Object.keys(choices) as (keyof typeof choices)[]) {
    if ((choices[key] as readonly unknown[]).includes(input[key])) Object.assign(result, { [key]: input[key] })
  }
  return result
}

function rgb(color: string): number[] {
  return [1, 3, 5].map(i => parseInt(color.slice(i, i + 2), 16))
}
function luminance(color: string): number {
  const [r, g, b] = rgb(color).map(n => n / 255).map(n => n <= .04045 ? n / 12.92 : ((n + .055) / 1.055) ** 2.4)
  return .2126 * r + .7152 * g + .0722 * b
}
export function contrastRatio(a: string, b: string): number {
  const la = luminance(a), lb = luminance(b)
  return (Math.max(la, lb) + .05) / (Math.min(la, lb) + .05)
}
function blend(a: string, b: string, amount: number): string {
  const right = rgb(b)
  return '#' + rgb(a).map((n, i) => Math.round(n + (right[i] - n) * amount).toString(16).padStart(2, '0')).join('')
}
function accessibleColor(color: string, backgrounds: string[], target = 4.5): string {
  if (backgrounds.every(bg => contrastRatio(color, bg) >= target)) return color
  const blackMin = Math.min(...backgrounds.map(bg => contrastRatio('#000000', bg)))
  const whiteMin = Math.min(...backgrounds.map(bg => contrastRatio('#ffffff', bg)))
  const toward = blackMin >= whiteMin ? '#000000' : '#ffffff'
  for (let i = 1; i <= 100; i++) {
    const candidate = blend(color, toward, i / 100)
    if (backgrounds.every(bg => contrastRatio(candidate, bg) >= target)) return candidate
  }
  return toward
}

export function resolveAppearanceMode(prefs: AppearancePreferences): 'light' | 'dark' {
  return prefs.mode === 'system'
    ? (typeof window !== 'undefined' && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
    : prefs.mode
}

/** Derived colors retain readable text even for very pale or very dark custom colors. */
export function buildAppearanceTokens(preferences: AppearancePreferences, resolvedMode: 'light' | 'dark' = 'light'): Record<string, string> {
  const prefs = normalizeAppearance(preferences)
  const mode = prefs.mode === 'system' ? resolvedMode : prefs.mode
  const bg = mode === 'dark' ? prefs.dark_background_color : prefs.background_color
  const lightCanvas = contrastRatio('#000000', bg) >= contrastRatio('#ffffff', bg)
  const surface = blend(bg, lightCanvas ? '#ffffff' : '#000000', lightCanvas ? .76 : .18)
  const sidebar = blend(bg, lightCanvas ? '#ffffff' : '#000000', .13)
  const backgrounds = [bg, surface, sidebar]
  const initialAccent = accessibleColor(prefs.accent_color, backgrounds)
  let accentSoft = blend(surface, initialAccent, .085)
  // Keep every surface on the same side of the canvas luminance. This also
  // supports colors at the exact black/white contrast crossover.
  if (lightCanvas ? luminance(accentSoft) < luminance(bg) : luminance(accentSoft) > luminance(bg)) accentSoft = blend(surface, bg, .5)
  backgrounds.push(accentSoft)
  const text = accessibleColor(lightCanvas ? '#25372e' : '#edf1ed', backgrounds, 7)
  const muted = accessibleColor(lightCanvas ? '#6b746d' : '#a5b1a8', backgrounds)
  const accent = accessibleColor(prefs.accent_color, backgrounds)
  const onAccent = contrastRatio('#ffffff', accent) >= contrastRatio('#000000', accent) ? '#ffffff' : '#000000'
  const fonts = {
    inter: '"Inter Variable", Inter, sans-serif',
    manrope: '"Manrope Variable", Manrope, sans-serif',
    serif: '"Source Serif 4 Variable", Georgia, serif',
    system: 'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
  }
  return {
    '--fc-bg': bg, '--fc-surface': surface, '--fc-sidebar': sidebar,
    '--fc-text': text, '--fc-muted': muted, '--fc-border': blend(surface, text, .17),
    '--fc-accent': accent, '--fc-accent-original': prefs.accent_color,
    '--fc-accent-soft': accentSoft, '--fc-on-accent': onAccent,
    '--fc-danger': accessibleColor(lightCanvas ? '#a53c32' : '#f19489', backgrounds),
    '--fc-success': accessibleColor(lightCanvas ? '#36594c' : '#8fb69b', backgrounds),
    '--fc-font': fonts[prefs.font_family],
    '--fc-heading-font': prefs.font_family === 'inter' ? fonts.serif : fonts[prefs.font_family],
    '--fc-message-size': { small: '.875rem', medium: '.9375rem', large: '1.125rem' }[prefs.font_size],
    '--fc-body-size': { small: '.8125rem', medium: '.875rem', large: '1rem' }[prefs.font_size],
    '--fc-radius': { soft: '12px', rounded: '22px', square: '2px' }[prefs.corner_style],
    '--fc-space': prefs.density === 'compact' ? '.82' : '1',
  }
}

export function applyAppearance(preferences: AppearancePreferences): void {
  const prefs = normalizeAppearance(preferences)
  const mode = resolveAppearanceMode(prefs)
  const root = document.documentElement
  for (const [key, value] of Object.entries(buildAppearanceTokens(prefs, mode))) root.style.setProperty(key, value)
  for (const element of [root, document.body]) {
    element.dataset.mode = mode
    element.dataset.density = prefs.density
    element.dataset.font = prefs.font_family
    element.dataset.fontSize = prefs.font_size
    element.dataset.corners = prefs.corner_style
  }
  root.style.colorScheme = mode
}
