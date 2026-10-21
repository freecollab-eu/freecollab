# File experience and desktop Office

Channel files now offer list and gallery views, lazy authenticated image
thumbnails, version-linked discussions and broader passive previews. Every
thumbnail and browser preview uses the same authenticated native download or
verified, client-decrypted Matrix attachment provider as the original file.
The browser does not send a file to an external preview/conversion service.
Gallery thumbnails are limited to JPEG, PNG, GIF and WebP images of at most
8 MiB. Preview components revoke Blob URLs and clear content when the signed-in
account changes or the component closes.

## Preview scope

The existing image and PDF previews remain available. Additional types are:

- UTF-8 plain text, JSON, Markdown, common source-code/XML/HTML/CSS/YAML MIME
  types, and CSV: at most 1 MiB. HTML, XML and code are escaped React text;
  scripts and tags never become active page elements. CSV parsing supports
  quoted commas, escaped quotes and newlines, and displays at most 200 rows by
  30 columns. Formula cells are displayed as text.
- Browser-supported audio (MP3, WAV, Ogg, WebM, M4A) and video (MP4, WebM, Ogg):
  authenticated, MIME-forced Blob URLs of at most 50 MiB. Codec support depends
  on the browser. Unsupported codecs produce a download suggestion.
- Native DOCX, XLSX and PPTX: passive extracted text, cached spreadsheet values
  and slide text. This is deliberately not a full-fidelity Office renderer.
  The server reads only specific XML entries; it never fetches relationships,
  images, external data, fonts or URLs and never runs formulas or macros.

OOXML previews and desktop editing accept at most 10 MiB compressed, 1,000 ZIP
entries, 8 MiB total expanded content, 1 MiB per entry, bounded compression
ratios and 100,000 displayed characters. Duplicate and escaping paths,
encrypted ZIPs, binary/macro payloads, DTDs, entities, non-UTF-8 XML and malformed
documents fail closed. Spreadsheet previews show up to ten sheets and 200 rows
per sheet; presentations show up to 30 slides. Rich documents can exceed these
conservative limits and remain downloadable. OpenDocument, legacy binary Office,
macro-enabled Office, CAD, archives and other unlisted formats do not gain a
native renderer. This release makes no “100+ formats” claim.

Encrypted Office attachments stay on trusted devices and remain download-only
for Office rendering; they are never submitted to the native OOXML extractor.
Text, code, CSV, audio and video previews in encrypted channels decrypt locally.

## Comments and versions

Native file comments are immutable, authenticated, bound to the channel,
document and exact file version, and encrypted with the server message key ring.
Current channel/workspace membership is checked on reads; writes also recheck
the signed-in account after taking the channel writer fence. Discussions follow
the same document across versions. An author label and original version
reference remain intact. Native discussions retain at most 100 comments.

Encrypted comments are authenticated `eu.freecollab.file.comment` Matrix events.
No comment text, filename index or plaintext comment request is sent to the
native comment API. The browser verifies room, event type, encryption,
redaction state, author identity, document identity and the original authenticated
attachment event before displaying a comment. It also checks that the selected
file remains in the current verified document history. The client displays the
latest 100 loaded comments; Matrix room history and its retention remain the
authority for encrypted content.

Native file bytes retain the existing storage semantics: they are stored without
application-level encryption. Native comment encryption does not change that.
Matrix attachment bytes and their metadata retain end-to-end encryption.

## Desktop Office editing

For a current native DOCX, XLSX or PPTX owned by the signed-in person, **Set up
Office editing** asks for the account password and an authenticator/recovery
code when configured. It creates a private 15-minute capability and an
`ms-word:ofe|u|…`, `ms-excel:ofe|u|…` or `ms-powerpoint:ofe|u|…` handoff. The URL
contains a random, scoped capability; the browser JWT is never in a URL or
desktop application. Only a SHA-256 digest of the capability is persisted.
The handoff requires HTTPS except for local loopback development.

The capability is restricted to one existing document, its owner, original
native account session and account-security version. Every DAV operation
rechecks current account/session revocation, workspace scope, membership,
ownership, document availability and the native/secure-channel fence. Expiry,
logout, account blocking, membership removal or explicit **Close Office session**
stops access. Passwordless managed accounts cannot use this password-based
handoff. Encrypted channels never issue native Office edit capabilities.

The single-resource WebDAV endpoint implements OPTIONS, depth-zero PROPFIND,
GET, HEAD, exclusive write LOCK/refresh, UNLOCK and PUT. A PUT requires either
an exact strong `If-Match` identity or a valid exclusive lock whose downloaded
baseline still matches the current head. HEAD and PROPFIND do not advance that
baseline. A stale save returns 412; another editor's lock returns 423; a missing
precondition returns 428. Native manual uploads can create a newer version while
Office is open; Office's stale baseline then fails rather than replacing it.
Office PUTs preserve the MIME type and revalidate bounded, macro-free OOXML.

Every accepted save creates a new immutable File/FileVersion and advances the
FileDocument head in the same writer transaction. It never overwrites original
bytes. A lost acknowledgement followed by a repeated stale conditional PUT
cannot duplicate or replace history. **Refresh saved versions** reloads the
normal file/version UI. Keep the capability URL private: it is an explicit
short-lived desktop grant, and its URL segments must be redacted from request
and audit logs. Responses use `private, no-store` and `no-referrer`.

This is a single-document WebDAV edit contract, not WOPI, SharePoint or Office
Online. Collection browsing, WebDAV copy/move/delete, temporary sibling resources
and simultaneous desktop coauthoring are unsupported. Desktop vendors can make
additional protocol requests; those operations fail closed. The available
environment has no desktop Microsoft Office binary, so automated proof covers
the actual handoff and network WebDAV open/lock/edit/saveback contract. Vendor
desktop acceptance has not been executed and is not claimed.

## Validation and integration

`backend/.venv/bin/python -m pytest tests/test_file_experience.py -q` from
`backend/` covers passive extraction, active-content/archive adversaries,
immutable comment identity, real DAV method handling, lock/precondition
conflicts, immutable exact-byte history and current authority revocation.
`node --test tests/file-experience.test.mjs` from `frontend/` covers CSV bounds,
preview routing, encrypted comment substitution attacks, account changes,
binding failures and Office confidentiality boundaries.

`CHROMIUM_PATH=/usr/bin/chromium backend/.venv/bin/python
frontend/tests/file-experience-browser.py --secure` starts private API/Vite
processes with temporary SQLite, storage and keys. It exercises actual file
previews, media codecs, authenticated thumbnails/comments, password
reauthentication, the Office handoff, real HTTP DAV saveback, exact old/new
bytes, mobile layout and personal Swedish UI. The secure extension uses two
trusted Rust Matrix devices, client-only HTML/code preview and encrypted peer
comments, and verifies actual `m.room.encrypted` wire content and absence of
native comment requests. It accepts explicit Matrix environment variables or
uses the existing retained homeserver with new isolated identities. It never
recreates that homeserver or changes its existing keys.

Root integration includes `api.file_experience.router`, the bounded Office PUT
body path in request middleware, capability-path log redaction, the expanded
MIME union in native and Matrix upload validation, and the new components in
the existing file list/preview. Components use per-person `useI18n()` and an
automatically registered Swedish catalog; user filenames, code and comments
are preserved as written.
