# Internal security and quality review — 2026-10-22

This review covers the Open Studio design candidate. Reproduced defects were fixed and given regression checks; a separate reviewer checked the content, widget, authentication and logging changes. This is an internal code review, not an independent external security audit or a cryptographic assessment.

## Findings and fixes

| Area | Reproduced problem | Resulting behavior |
| --- | --- | --- |
| Office downloads | Logout, blocking or removed membership during a storage read could leave GET/HEAD authorized by stale state. | Authority is checked again after I/O. Bytes and ETag still identify the immutable version actually read. |
| Matrix content references | Remote event reads could retain cached session, membership, administrator role or room/account identity. | A fresh identity map checks current authority and unchanged bindings on the caller's existing connection, preserving its pending changes and transaction locks. |
| Appearance/language saves | A request authorized before logout or blocking could save preferences afterward. | Both saves serialize on the account and recheck live authority, including after an insert-conflict retry. |
| Office ZIP parsing | Entry limits were applied after allocation; malformed directory names and comment signatures could confuse parsing. | A shared bounded directory scanner runs before `ZipFile`; malformed Office inputs return 422. Slack retains its existing limits and errors. |
| Legacy widget | Account/channel changes and synchronous host callbacks could retain old drafts, paint old history or let old request cleanup alter the new binding. | Binding changes clear private UI and cancel old work; generation checks also surround host callbacks. Destroyed instances cannot restart. |
| Widget transport | API URL and browser request defaults allowed weaker bearer transport. | HTTPS is required outside HTTP loopback development; credentials in URLs, queries, fragments and redirects are refused. Requests omit cookies and disable caching. |
| Account creation | Registration and administrator account creation accepted one-character passwords. | New native accounts require at least 12 Unicode code points and at most 72 UTF-8 bytes. Existing short-password sign-in/reauthentication and customer SSO remain supported. |
| Access logs | Four OAuth/OIDC callbacks could log authorization codes and state. | All five registered callback paths redact their query, including optional API prefixes and encoded/trailing-slash variants; normal log metadata remains. |
| Integration transport | Webhook DNS had an unbounded pending queue, proxy resolution could exceed the delivery deadline, and nested proxy TLS could select the wrong server name. | Webhooks share bounded public-DNS transport; proxy resolution uses the delivery deadline and each TLS layer verifies its own hostname. Deeply nested invalid provider JSON returns a typed error. |
| Native build dependencies | Desktop/mobile development dependency trees contained moderate advisories. | Scoped, compatibility-tested overrides remove the reported advisories without changing application runtime APIs. |

Quality changes also consolidate the bounded ZIP scanner and preference authorization lock, translate administration navigation, show the actual package version in administration, and prevent an unused CUDA download in the Railway frontend build.

## Executed validation

Focused runs overlap and must not be added together as a unique test total.

- Content: 92 Office/file/Slack tests and 152 Matrix/document/caller tests passed. A separate PostgreSQL 15.19 fixture passed 29 cases, including actual concurrent row locks and preservation of the caller's unflushed changes on success and denial. Another reviewer ran 12 ZIP compatibility/boundary checks and two Office version-identity checks.
- Identity: 94 authentication/account/administration/API tests and 156 SSO/embed/direct-access tests passed. Eight browser groups checked Unicode boundaries, legacy sign-in, administration and Swedish/mobile navigation.
- Integration transport: 118 tests passed, including HTTP/HTTPS proxy connections and bounded resolver behavior.
- Logging: 126 focused logging/OAuth/language/calendar tests passed. Another reviewer independently passed 71 authentication/logging cases.
- Widget: 21 unit tests and nine actual Chromium security groups passed, covering reentrant callbacks, rebinding, held sends, cookie omission, caching, redirects and destroyed-instance lifetime. Another reviewer independently reran the 21 unit tests.
- Design: 30 actual browser groups passed for demo drafts, language/account preferences and dense real conversations; 17 connection-guide groups passed after fixing Swedish button overflow at 320 pixels. The standalone offline demo passed 16 groups plus an additional Swedish 320-pixel check.
- All 289 frontend unit tests passed. Production application/embed, website and widget builds passed.
- Full dependency audits reported no advisories for the frontend, SDK, desktop or mobile npm trees and the exact 87 Python development pins on this date. These are dated advisory-database observations. See [DEPENDENCIES.md](DEPENDENCIES.md) for scope and native toolchain compatibility checks.

The full backend regression result and GitHub workflow state are recorded with the pull request. Full CI must be assessed for the exact candidate commit; an earlier workflow is not evidence for later source changes.

## Boundaries

Tests used disposable databases, storage and local transports. The PostgreSQL fixture was removed; retained pilot databases, keys and services were not changed. Real customer IdPs, Microsoft/Google integrations, Matrix hosting and external object-storage services were not penetration tested. Existing encryption protocols were not cryptographically audited. Browser, dependency and code checks do not establish complete accessibility, denial-of-service resistance or deployment readiness. Linux native package/synchronization checks do not establish signed macOS/Windows builds, physical-device behavior or store acceptance.

The [security model](SECURITY_MODEL.md) describes the continuing content, credential and deployment boundaries. Railway deployment and production TLS, backup scheduling, provider credentials and independent review remain separate deployment work.
