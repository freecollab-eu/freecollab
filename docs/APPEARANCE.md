# Personal appearance

The reference client starts with warm stone, ink and a vermilion accent. Each person can adapt this appearance without changing another member's view or the team's shared content. These settings belong to the signed-in account, rather than a channel or installation. Existing saved appearances remain intact when defaults change.

## Use the appearance panel

1. Sign in and choose **Appearance** in **More channel tools** or the channel drawer, or use **Cmd/Ctrl+K** and choose **Appearance settings**.
2. Use **Colors**, **Typography** and **Layout** to move between the controls. The live preview stays visible as you scroll. Choose **Light**, **Dark** or **System** under Colors; System follows the device's color-scheme preference, including changes while the app is open.
3. Start with **Studio**, **Linen**, **Fjord**, **Clay** or **Ink**, or use the color picker and six-digit hex fields for your accent and canvas. Studio is the default; the original four palettes retain their colors. Each palette supplies a light and dark canvas. The canvas control edits the currently displayed mode; switch modes to edit the other canvas.
4. Choose **Inter**, **Manrope**, **Source Serif** or **System**, then set text size, Comfortable/Compact spacing and Soft/Rounded/Square corners.
5. Check the live preview. **Save changes** saves the complete selection to your account and shows **Appearance saved to your account** after the server confirms success. **Cancel**, the close button, Escape or closing the backdrop restores your saved appearance. **Reset** previews the default Studio appearance; choose **Save changes** to keep that reset.

While the panel is open, keyboard focus stays inside it and the background controls are inactive. Tab and Shift+Tab move through its controls; Escape closes it and returns focus to the trigger. The preview uses the same shared four-leaf FreeCollab mark as the sign-in and chat views.

Inter, Manrope and Source Serif 4 ship with the frontend. System uses the device's installed font stack. There are four type choices and no requests to a font CDN; custom font URLs and arbitrary CSS are not supported. Headings use the selected typeface, including Inter by default.

Saving requires a successful authenticated API request. A failed save keeps the preview available for retry. Loading failures show a retry action; a cached appearance may still be shown. The account's server copy is authoritative, and saved settings load when the app starts or the account changes. A separate cache for each account in browser local storage helps display its last loaded appearance. Changing accounts clears the preceding account's active preview. This does not provide live synchronization between different devices.

## Colors and readability

The app derives text, surfaces, borders and button colors from the saved colors. It adjusts accent text and borders where needed to improve contrast, so those roles can differ from the stored hex value. Original-color buttons use a separately derived readable foreground. The selected values remain available in the color controls.

Automated token tests check at least 4.5:1 contrast for the tested body/muted/accent text and accent-button pairs across the built-in palettes and varied custom canvases. These checks support readability; they are not a full accessibility audit of every interface state, image or embedded widget. Text size and spacing controls apply to the reference client, while the standalone widget keeps its host integration styles.

## Authenticated API

The API exposes `GET /preferences/appearance` and `PUT /preferences/appearance`. Through the bundled frontend proxy these become `/api/preferences/appearance`. Both require an active account's bearer token and return `Cache-Control: private, no-store`. Blocked accounts and revoked sessions are rejected using the normal authentication rules.

GET returns the saved snapshot or these defaults without creating a database row:

```json
{
  "version": 1,
  "mode": "light",
  "accent_color": "#ee593f",
  "background_color": "#f1eee8",
  "dark_background_color": "#252321",
  "font_family": "inter",
  "font_size": "medium",
  "density": "comfortable",
  "corner_style": "soft"
}
```

PUT requires this complete snapshot and returns the validated saved snapshot. It replaces the account's settings; it does not merge a partial update. Send the default snapshot to reset an account.

| Field | Accepted values |
| --- | --- |
| `version` | Integer `1` |
| `mode` | `light`, `dark`, `system` |
| `accent_color`, `background_color`, `dark_background_color` | Exactly `#` and six hexadecimal digits; normalized to lowercase |
| `font_family` | `inter`, `manrope`, `serif`, `system` |
| `font_size` | `small`, `medium`, `large` |
| `density` | `comfortable`, `compact` |
| `corner_style` | `soft`, `rounded`, `square` |

Missing fields, unknown fields, unsupported values, CSS expressions and font URLs return validation errors (`422`). The API derives ownership from the authenticated account. It accepts no `user_id`, and a caller cannot select another account's preferences. Concurrent first saves resolve to one account row; a later successful save replaces the previous snapshot.

The backend exports `AppearancePreferences`, the required-field `AppearanceUpdate` schema and `DEFAULT_APPEARANCE` in `app/api/preferences.py`.

## Storage and upgrades

`UserAppearance` is a separate table with an account foreign key, a validated JSON snapshot and an update timestamp. Startup's existing `SQLModel.metadata.create_all` creates this additive table when missing; it does not alter the existing user table or require changes to existing account data. Unconfigured accounts continue to receive defaults. This release does not introduce an incompatible user-table migration.

Preferences are ordinary database records, rather than encrypted message bodies. The existing full-database backup includes them; there is no separate appearance file or key to preserve. Follow [OPERATIONS.md](OPERATIONS.md) for database, uploaded-file and content-key recovery. Browser caches are a convenience and do not replace the database backup.

## Validation

The focused backend suite covers authenticated reads and writes, defaults without row creation, complete-snapshot validation, account isolation, blocked/revoked sessions, persistence across fresh sessions and simultaneous first saves. The frontend appearance suite checks allowed settings, normalization, mode/DOM updates and derived color contrast.

The Studio update passed 27 backend preference cases and the five frontend appearance cases within the 276-case client suite. Six dedicated browser groups exercised the real appearance provider and preferences router with disposable accounts: preview/cancel, authenticated save, account isolation and reload, failed saves, responsive controls and system/account changes. The real-client interaction checks also covered custom dark, large-type dialogs and focus restoration at 320/390 px. These scoped checks are separate from the earlier release evidence in [VALIDATION.md](VALIDATION.md).
