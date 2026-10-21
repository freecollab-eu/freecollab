export type Locale = 'en' | 'sv';
export type LanguageChoice = Locale | 'auto';
export type Variables = Record<string, string | number>;
export type Catalog = Record<string, string>;

const catalogs: Record<Locale, Catalog> = { en: {}, sv: {} };
const listeners = new Set<() => void>();
let active: Locale = 'en';
export const languageChoices = [
  { id: 'auto', name: 'Browser language' },
  { id: 'sv', name: 'Svenska' },
  { id: 'en', name: 'English' },
] as const;
export function isLanguageChoice(value: unknown): value is LanguageChoice { return value === 'auto' || value === 'en' || value === 'sv'; }
export function resolveLanguage(choice: LanguageChoice, languages: readonly string[] = typeof navigator === 'undefined' ? ['en'] : navigator.languages): Locale {
  if (choice !== 'auto') return choice;
  for (const language of languages) {
    const base = language.toLowerCase().split('-')[0];
    if (base === 'en' || base === 'sv') return base;
  }
  return 'en';
}
export function registerCatalog(locale: Locale, entries: Catalog) {
  for (const [key, value] of Object.entries(entries)) if (typeof value === 'string') catalogs[locale][key] = value;
}
export function currentLocale() { return active; }
export function subscribeLocale(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; }
export function applyLocale(locale: Locale) {
  if (typeof document !== 'undefined') { document.documentElement.lang = locale; document.documentElement.dir = 'ltr'; }
  if (locale === active) return;
  active = locale;
  for (const listener of listeners) listener();
}
/** Only application-owned source strings belong here; never translate user content. */
export function t(source: string, variables: Variables = {}): string {
  const translated = Object.prototype.hasOwnProperty.call(catalogs[active], source) ? catalogs[active][source] : source;
  return translated.replace(/\{([A-Za-z][A-Za-z0-9_]*)\}/g, (whole, key: string) => Object.prototype.hasOwnProperty.call(variables, key) ? String(variables[key]) : whole);
}
export function formatDate(value: string | number | Date, options?: Intl.DateTimeFormatOptions) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? t('Not available') : new Intl.DateTimeFormat(active, options).format(date);
}
export function formatNumber(value: number, options?: Intl.NumberFormatOptions) { return new Intl.NumberFormat(active, options).format(value); }
