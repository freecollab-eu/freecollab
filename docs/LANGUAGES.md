# Personal language selection

FreeCollab 0.9 lets each person choose **Svenska**, **English**, or **Browser language**. The selector appears before sign-in and in account settings. The authenticated choice is stored by `/preferences/language` and follows the account across devices, including customer-owned workspace SSO sessions. An anonymous browser choice stays separate from account preferences.

Switching language updates React views and the semantic document language without remounting the composer, resetting appearance or translating user-authored names, messages, polls or documents. Dates, numbers and relative notification times follow the selected locale. Unsupported browser locales use English. External provider interfaces and unregistered server/provider error strings may remain in their original language.

Account-bound local caches make the selector usable when storage or the network is unavailable. An unsaved choice shows a retry state. Saves are ordered per account; a stalled request from another account cannot block the active account. Authentication generations and request versions reject delayed loads and save responses from earlier identities. The authenticated API validates a finite locale allowlist and repeats live account/session checks after its database writer lock.

## Adding another language

Application-owned text uses `useI18n().t('English source', {count})`; user content is rendered directly. Keep interpolation placeholders intact. Feature-owned Swedish catalogs live in `frontend/src/i18n/*.sv.ts` and are bundled locally; the product never sends collaboration content to a translation provider. English source text is the fallback.

Add a reviewed catalog, register its locale in `frontend/src/i18n/core.ts` and the catalog registry, then extend `LanguagePreferences` and `/capabilities.languages` in the backend. Add the language's direction/plural/date policy and run the catalog, accessibility and account-isolation browser checks before advertising support. The current shipped allowlist is `en`, `sv`, and the `auto` preference.

See `frontend/tests/i18n.test.mjs`, `frontend/tests/language-browser.py` and `backend/tests/test_language_preferences.py` for validation. UI localization does not translate emails or add message translation.
