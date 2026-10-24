# FreeCollab validation record

## Commands in thread reply fields — 2026-10-24

A Swedish mobile screenshot exposed a missing composer integration. The exact
previous commit `d44aae3` was reproduced in a disposable native thread: `/docs`
showed no palette, left Send enabled and was persisted as a literal reply on
Enter. The correction uses one surface-scoped command parser and palette for
both main messages and thread replies, with guarded send paths and normal
permission checks. Bare commands browse existing work; argument commands open
review drafts. Explicit work Close can restore the originating live thread.
Main activity, a newer thread or a changed identity invalidates older returns.
Thread scheduling opens a parent reminder and never schedules the reply as a
channel message.

Validation:

- All 600 frontend Node tests passed, plus TypeScript and production application
  and embedded builds.
- Eleven actual native/demo browser groups passed, with 596 copied source hashes
  per fixture and zero drift. They cover `/docs` keyboard and touch selection,
  exact thread return, independent main/thread drafts, thread `@` task assignee
  and explicit save with canonical parent link, ordinary referenced replies,
  Escape/literal opt-out, direct submit protection, declined dirty navigation,
  superseding main `@` activity, identity/thread/channel changes and Swedish
  390/320/short-viewport bounds. The owned demo visit was revoked with HTTP 204.
- All 18 existing main-command/reference browser groups also passed unchanged
  against the same runtime: 590 wrapper hashes and 595 actual native/demo source
  hashes per fixture, with zero drift and normal fixture cleanup.
- Seven real-hook scope tests passed. The unchanged old hook fails exactly the
  three stale-thread response/selection/focus cases; optional thread context
  now prevents reuse across identical drafts in the same channel.

The new browser regression is wired into Product CI. The prior complete
`d44aae3` workflow passed frontend, all four backend shards, real-client
cryptography/recovery and the full product browser workflow before this fix.


## Recent-read history pagination fixture — 2026-10-24

The 122-root history proof now records and verifies a genuine recent own receipt
before entering the channel. This keeps its original older-message pagination,
search, edits, deletion, mentions and storage assertions meaningful with the new
receipt-based entry policy. All 4 original Python assertions and 15 browser
expectations remain; the canonical-receipt assertion is additional.

The actual history proof passed all 4 groups. Files, collaboration, office files
(7 groups) and security operations (9 groups) also passed in disposable native
fixtures with no source drift. Read-only review of all 66 Product CI browser
scripts found no further affected fresh-root pagination fixture. The preceding
CI candidate passed frontend, all four backend shards and real-client encryption
and recovery; its Product job identified this fixture adjustment. Final workflow
status is tracked on the review branch.

## Feature-list keyboard and pointer intent — 2026-10-24

A real Swedish 390 px owner flow exposed keyboard scrolling triggering trusted
mouse-enter events under a stationary pointer, which changed the active command
while the combobox retained focus. Hover selection now responds to actual mouse
movement. Clicking still selects explicitly.

TypeScript and 35 focused command tests passed. All 18 language-browser groups
passed against the patched source, retaining all 32 original Python assertions
and adding a deterministic stationary-pointer Home/End/Arrow regression plus
actual later mouse motion. The test also verifies that a real member cannot see
owner-only data controls, then uses a real owner session to open all 11 work
features at 390/320 px. No role override, pointer-away workaround or automatic
resource write is used. The preceding 33-group full product proof remains bound
to its recorded source candidate; follow-up work-view/flow runs record the narrow
pointer change separately.

## Everyday work follows the conversation — 2026-10-24

The six [conversation-first UX changes](FLOW_FIRST_UX.md) are implemented in the
shared product interface: receipt-based/scoped reading entry, a quiet composer,
read-first documents, actual discussion work summaries, clear channel navigation
and concrete contextual AI review. Saved document modes retain the same editor
and live session; opening review never sends to a provider or applies a result.

Executed against the final runtime candidate:

- TypeScript and application/embedded production builds passed; all 593 frontend
  Node tests passed, including the 20 entry-policy cases.
- Ten actual native/demo flow groups passed with 596 source hashes per fixture
  and no drift. They include a 65-message paged unread boundary and remembered
  message/offset, genuine delayed cloud errors, read/edit/Yjs/comments, work from
  unopened replies, explicit contextual AI, measured full mobile channel names,
  membership loss and the isolated Swedish 320 px demo.
- Three of those groups exercise actual network timing: an older receipt POST
  is held while a real peer arrives; the newer receipt waits and remains current
  after release. A failed receipt GET preserves unread state through pointer and
  composer events until explicit Jump to latest. Sending during held initial
  history wins over the older unread plan. Production handlers are not replaced.
- The configured private-provider browser proof passed all 17 groups (590 source
  hashes); the real two-member document/offline/Yjs proof passed all 8 groups
  (585 hashes), each without source drift. These are private fixtures, not live
  external-provider or Matrix acceptance.
- Final native Elite navigation/reading passed 6 groups, commands and references
  passed 18 native/demo groups, and document organization passed 15 groups.
  Eleven legacy browser adapters retain all 234 original Python assertions,
  with 2 additional measured/receipt assertions. They now use actual channel
  menus, `/`, read/edit transitions and role-permitted feature entry.
- The 55 focused backend discussion tests and 17 resolver/hook tests remain
  applicable: all 12 recorded source hashes match. Derived native routing stays
  bounded, same-channel and content-free; no migration or encrypted plaintext
  fallback was introduced. The dedicated cloud focus proof passed 13 cases.

Read-only review caught and checked fixes for stale source/AI frames, early own
sends, unknown/locked read boundaries and a real concurrent receipt-write race.
Read writes are now serialized and coalesced per scoped channel. The new flow
and delayed-cloud-focus browser proofs are included in the Product CI job.
The full product, language and work-view browser suites retain their existing
business, privacy, focus, write and reading-anchor checks through the new entry
controls; their run outputs and publication verification are recorded separately.


## Returning from work retains the reading position — 2026-10-23

The public style acceptance caught a mobile source return that moved 90 px.
A private actual-product reproduction confirmed that the source position was
first restored correctly; a queued composer measurement then treated its
distance of less than 96 px from the bottom as a choice to follow latest.
Closing back to an unchanged authorized source now retains the explicit
reading choice before those measurements run. Deliberate chat navigation,
own sends and **Jump to latest** retain their normal precedence.

- **543 frontend cases passed**, together with TypeScript and application/
  trusted-embed production builds.
- All **17 actual native/example-product workspace groups passed**, including
  two added regressions with the original 15 groups preserved. The genuine
  source/footer offset remains 102.203125 px after save, all work modes,
  minimization and explicit close. The 90 px distance from the bottom remains;
  it is not replaced by following. Deliberate message reading while work is
  parked remains in place, and actual own-send/jump actions still follow.
  All 586 source hashes per fixture match; both owned example visits were
  revoked with HTTP204 and the fixtures removed.
- The unchanged **15-group conversation regression passed** with all 576
  recorded source hashes matching. Current-source screenshots were reviewed.
  All 59 stylesheets recorded by the earlier 11-group visual proof remain
  byte-identical; this increment changes the return behavior and its tests.
- The next CI startup failure was reproduced in the isolated external-meeting
  fixture with the same missing scheduled-message model. Its explicit model
  import fixes startup; production model initialization is unchanged. All
  **nine external-meeting browser checks passed** using private TLS provider
  fixtures, including create/recovery/cancel/calendar and Swedish mobile.
  These are not live customer-provider consent tests. An audit accounts for
  all 11 explicit browser database launchers: the other launchers register the
  full model graph, and the meeting fixture has its earlier explicit fix.
  Original containers remain unchanged; owned fixtures are removed.

The failed public attempt and its cleanup remain preserved separately. Public
acceptance is recorded against the new frozen source; the earlier 10-of-11
result is not substituted for that source's final acceptance.

## Shared interface polish — 2026-10-23

Conversation, documents, stacks, tasks, decisions, account/appearance settings,
discovery menus, authentication and public surfaces share a refined type and
control scale. The mobile composer keeps its three labeled discovery controls
in one row with 44 px targets. Provider accounts, federated work and imported
history now use the actual personal theme tokens. The existing approved layout,
message order, editor sessions, permissions and explicit saves remain intact.

Executed against the final styles:

- All **24 changed stylesheets parsed successfully**. TypeScript and the
  application/trusted-embed production builds passed.
- All **15 real native/example-product workspace groups passed**, with 586
  source hashes per installation matching the final runtime. The same editor,
  pending-write guards, draft/selection/scroll retention, typed references and
  saved Swedish at 320 px remain covered. The owned example visit was revoked
  with HTTP204 and its disposable fixture removed.
- **11 additional real native browser groups passed**, with the same 586-source
  runtime snapshot. They save and reload custom light/dark palettes and actual
  Manrope/Source Serif preferences through ordinary account APIs; inspect long
  content, two real documents placed in one stack, command/reference menus,
  account keyboard focus, auth/invite forms, and three Swedish mobile work
  modes. Provider-account views show actual empty/unconfigured states without
  external consent or customer credentials. All three mobile discovery buttons
  share one row with targets at least 44 px; tested text pairs have measured
  contrast above 5.4:1. Reduced-motion behavior, narrow bounds and 34 actual
  screenshots were reviewed. No browser errors, unintended external requests or
  HTTP429 responses occurred. This is targeted browser validation, not an
  independent complete accessibility audit.
- The previous CI meeting failure was reproduced before readiness: its minimal
  fixture registered `ScheduledMessageReference` without the `ScheduledMessage`
  model. An explicit fixture import fixes that metadata dependency; production
  initialization already imports it. Both services then start, and all **12
  actual WebRTC/media/guest checks pass**, preserving the original assertions.
  A private loopback TURN relay supplies media connectivity without changing
  managed browser policy. Startup errors now report bounded process statuses
  and exception classes without raw private logs. Original containers remain
  unchanged and owned fixtures are removed.

No product TypeScript, backend behavior, schema migration or provider consent
flow changed. The shared design guide documents the type tokens and controls.

## One workspace, three views — 2026-10-23

[Workspace modes](WORKSPACE_MODES.md) adds shared split, expanded and minimized
views to the retained conversation work area. Documents, tasks, decisions and
channel groups use the same controls. The actual editor and session stay
mounted; the footer sets work aside and the separate X retains its normal
close guards. Native/encrypted routing and backend data models are unchanged.

Executed for this increment:

- **543 frontend Node cases passed**, together with TypeScript and application/
  trusted-embed production builds.
- **Seven isolated real React/Chromium hook groups passed**. They verify retained
  DOM, pointer and keyboard focus, selection and scroll, both resume modes,
  nested legacy dialogs, popup/default-prevented Escape, IME, tab containment,
  scope changes, unmount cancellation and deliberate newer focus. This fixture
  exercises the hooks; it is not substituted for a real document session.
- **15 actual product browser groups passed**: 12 native and three genuine
  example-product groups, with 586 source hashes per fixture matching the final
  runtime. They retain the same editor nodes through all three views, create
  and synchronize real Yjs documents, hold and then deliver a genuine pending
  update, preserve all four editor types, send typed-reference chat while work
  is parked, reveal real source messages and enforce authoritative account/
  channel changes. English and saved Swedish at 320 px remain usable. The own
  example visit was revoked with HTTP204 and all fixtures removed.
- The **six-group elite**, **15-group conversation**, **seven-group slash** and
  **18-group typed-reference** regressions passed. Their actual executed-script
  and runtime manifests match the current source. Four close-intent selectors
  now use the real header X; every old behavioral assertion remains. An earlier
  aggregate with unused harness hashes is retained separately and is not the
  final binding.
- The complete **33-group example-product regression passed**, with all 579
  runtime source hashes matching. Five close-intent clicks now use the header
  X; all 33 original groups and 195 assertions are preserved. It includes
  visitor isolation/reset, draft and reading anchors, document synchronization,
  workflows and saved Swedish at 320 px. There were no HTTP429 responses, and
  the owned fixture processes and temporary data were removed.
- The earlier CI failures were reproduced and corrected at their actual
  fixture boundaries. **Four history groups passed** with unchanged search,
  live-edit/delete, pagination and recipient assertions plus an exact selected
  person-reference check. The real **14-check two-organization Connect/Matrix
  proof passed** against the reviewed SDK artifact and current source. It uses
  real signed managed fixture identities, not live customer IdP popups. All
  five original containers retained their IDs, start times and running state.
- **Five artifact-binding cases passed**: the Connect harness retains an
  explicit reviewed SHA-256 pin and also verifies all packed public bytes and
  the current SDK source digest. Tampered archives, source drift and stale
  distributions are rejected. No SDK runtime, version or publication changed.
- Both new browser proofs and the artifact-binding checks are wired into CI.
  The workspace contract, design guide, conversation contract, feature audit
  and README describe the shared view and close behavior.

The conversation keeps its existing order: newest messages sit at the bottom,
next to the composer. Reading older messages retains the reader's position;
this increment does not reverse the timeline. All 204 relative documentation
links checked across the updated guides resolve locally.

## Command arguments and typed references — 2026-10-23

The conversation composer accepts [command arguments](SLASH_COMMANDS.md) and
[typed references](MENTIONS_REFERENCES.md). Task, document, decision and search
commands prefill their actual native interfaces. `@` chooses people, real
channel notification groups, documents, tasks, files or the current channel in
messages, thread replies and message edits. Creation and changes still require
normal explicit saves.

Executed for this increment:

- **543 frontend Node cases passed**, including command parsing, explicit
  person identity, typed UTF-16 spans, secure resource resolution, rich draft,
  outbox, edit and encrypted payload checks. TypeScript and application/trusted
  embed production builds passed.
- **53 focused backend cases passed** against frozen source, covering current
  authority, UTF-16 validation, same-channel isolation, actual group membership,
  native writes/threads/offline idempotency, scheduled delivery, export and
  erasure. **180 related backend regression cases passed** before the final
  small reference-label/scheduling cleanup; the 53-case run covers the final
  backend source. These are targeted runs, not a new complete backend suite.
- **56 focused transport cases passed**, including exact historical encrypted
  file lookup, actor changes during routing, reference-preserving edits and
  queue payloads. Unit fixtures isolate external Matrix and storage boundaries;
  they do not substitute for a real customer Matrix/device acceptance run.
- **18 actual browser groups passed** in independent disposable native and
  example-product installations, with **582 source hashes per installation**
  matching the final runtime. They exercise actual prefilled command saves,
  first-project continuation, all six reference types, group CRUD/current
  recipients, thread replies, edits, exact resource opening, unavailable
  targets, rich draft reload, real offline delivery, genuine account changes,
  and saved Swedish command/reference interaction at 320 px. IndexedDB
  inspection confirms AES-GCM-256 draft ciphertext, a 12-byte IV and a
  nonextractable key; the same draft then reloads and sends with its bound IDs.
  All owned fixtures were removed and the owned example visit revoked204.
- The unchanged **six-group elite**, **15-group conversation** and
  **seven-group slash-discovery** browser suites also passed against the final
  runtime, each with 579 bound sources and no mismatch. Their existing IME,
  reading-position, profile-photo, draft, permission and appearance checks remain.
- The public component SDK exposes optional reference types and arguments on
  send/reply/edit while preserving old call signatures. **30 SDK checks passed**,
  including actual build, consumer declarations, serialized identities and
  account changes during authorization. No SDK version or publication changed.
- The self-contained new browser proof is included in CI. README, design,
  command and reference contracts and feature audit are updated; 203 local
  documentation links resolved during review.
- Read-only integration review corrected stale edit acknowledgements, IME
  selection fences, unauthorized group-draft consumption and genuine encrypted
  historical-file opening. A recognized command keeps its identity while
  arguments are entered, including after a mouse selection from `@`.
  Reference selection binds stable IDs, and group
  recipients are expanded from current membership at actual delivery.

The existing secure-device/provider acceptance requirements remain. Native
channels retain server encryption at rest; these references do not turn them
into E2EE channels or grant resource access. See the reference contract for
same-channel scope, current limits and on-device encrypted scheduling.

## Composer feature discovery — 2026-10-23

The shared product composer now opens the [available feature list](SLASH_COMMANDS.md)
with `/`, or through an explicit search that preserves an existing message.
Its 35 defined entry points are filtered by the current account, channel,
workspace and embedding scope; the visible count varies with that context.
The normal editors, approval forms and encryption clients remain authoritative.

Executed for this increment:

- **484 frontend Node cases passed**, including ten new parser, bilingual
  matching, complete-inventory, permission and encrypted/embedded-context cases.
- TypeScript, application, trusted-embed and demo production builds passed.
- The existing complete **six-group composer regression** also passed on 567
  unchanged bound files: multiline/IME, exact-once outbox recovery, reading
  position, quick switching, modal focus and 320/390 px custom-theme behavior.
- **Seven actual browser groups passed**, using independent disposable native
  and example-product installations. Each snapshot binds **569 source files**,
  with zero current-source mismatches. The checks open real document/task
  interfaces, verify no automatic message/resource write, preserve both drafts
  and search focus after a rejected work change, and await actual seeded task
  titles in the example installation.
- The same run verifies unknown-query Enter/Escape behavior, ordinary URLs,
  prose and multiline messages, channel-scoped drafts, genuine account-session
  replacement, current member permissions, saved Swedish language, all entries
  reachable in the 320 px list and no horizontal overflow. Owned example visits
  are revoked, and temporary API/Vite processes, databases, files and keys are
  removed.
- Read-only review corrected two additional UI issues: federated-work
  eligibility is reverified on a new opening, and rejected explicit-search
  navigation restores the search input. Selection scrolling depends on the
  selected entry rather than unrelated conversation rerenders.
- The existing CI browser job now runs the self-contained slash harness. The
  feature contract, audit, design system and README are updated; **193 relative
  links** in the affected documentation resolve.

These checks do not execute a customer's installed app, AI provider or
federated connection. Their existing external acceptance requirements remain.
The earlier 33-group demo and 15-group conversation runs below describe their
own recorded source snapshots; they are not substituted for this increment's
seven-group proof.

## Work inside the conversation — 2026-10-23

The actual signed-in product and example installation now open native document,
task and decision work beside their usable conversation. Message-derived work
can be reopened at its source. The [flow contract](CONVERSATION_WORK.md) describes
explicit saves, contextual management, native-only routing and current limits.

Executed overlapping scopes for this increment:

- **33 actual demo UI/API groups passed**, with all **558 bound source files**
  matching the final runtime. Real source-created documents and tasks use the
  persisted routing index; decisions retain canonical source provenance. Work
  reopens after channel changes, native Yjs editing and message sending retain
  the same editor DOM, dirty navigation is guarded, and the retained-draft
  Swedish 320 px editor remains usable. The original worker, files, portraits,
  provider boundary, independent visitor and reset checks remain. Actual request
  pacing keeps the unchanged public limits; **zero HTTP 429 responses**, unexpected
  external requests, retained credential accesses or browser errors occurred.
- **474 frontend Node cases passed**, together with TypeScript. These include
  scoped navigation guards, strict pending-write protection, atomic native and
  encrypted document creation, exact resource targeting, and AI draft boundaries.
- **15 normal-product browser groups passed**, with all **555 bound files**
  unchanged through the run. Existing continuity, account isolation, profile-photo
  and Swedish 320 px checks remain. New actual flows retain the same editor DOM
  while sending a message, hard-block pending native writes, and preserve unsent
  document/task/decision drafts across work, channel, workspace and exact-target
  navigation. Real task and decision saves persist through their native APIs.
- **Five built-App handoff groups passed** with **330 source hashes and 480
  complete build-input hashes** unchanged. Application and trusted-embed builds
  passed. The exact native product PNG, dimensions, real iframe handoff,
  English/Swedish, reload and documentation remain checked; expected iframe
  navigation is inspected and aborted before a sample visit.
- **136 backend channel/security/routing cases passed** on their final bound
  source. Eight new real registered-user cases create native rooms and both DM
  entry points, then share actual documents, tasks and decisions. New native
  rooms no longer receive an unused legacy envelope. Existing legacy envelopes
  remain untouched and still reject the native routing API. The routing slice
  includes fresh membership/DM-block races, unavailable targets, idempotency,
  authorization, export and retention. A separate **19-case demo suite passed**.
- Existing native browser suites passed after moving their real entry points:
  projects/tasks **10**, shared documents **7**, document organization **15**,
  workflows **6**, activity **3**, Copilot **15**, language **16**, apps **9**,
  data controls **6**, personal home **7**, native push **7** and calling **11**.
  Native push/calling use isolated vendor/device fixtures; these results do not
  establish physical-device or live customer-provider acceptance.
- The actual Swedish 320 px mobile editor retained a multiline conversation
  draft and exposed **255 px of document text**, compared with 40 px before the
  intent-aware readiness scroll. Later composer/comment focus remains respected.
  The same visibility assertion was retained rather than reduced.
- The complete existing room browser passed **five checks**, including a held
  real file response that cannot reveal old private material after channel change.
- A freshly built standalone SDK archive was checked against all **59 packed
  file bytes**, exact inventory and canonical file modes; its source digest
  matched all **23 SDK source files**. The encrypted Connect proof retains its
  strict exact-package SHA assertion with that verified archive. This artifact
  check is separate from a complete real-encryption browser run.

The fixtures use disposable accounts, databases, generated keys and owned local
services. Earlier sections describe their historical source states, rather than
additional complete runs of this increment. Public deployment and exact-head CI
are recorded separately; no release or main-branch merge is implied. An unchanged
seven-group communication proof passed on the final source, as did a diagnostic
run. One earlier historical-reply focus assertion failed intermittently and was
not reproduced; no runtime fix for that observation is claimed.

## Shared product room and personal photos — 2026-10-23

**Historical source:** this section predates the inline conversation-work increment above.

The actual product room keeps its conversation and composer mounted beside
existing channel files, one shared document with its real Yjs excerpt and stack,
and an unfinished native task. These cards open the normal editors, stacks,
task and file views. The approved Courtyard example uses actual product records
and authenticated image bytes. [The demo contract](PRODUCT_DEMO.md) describes
the same layout, its bounded read-only overview and the public account limits.

Executed overlapping validation scopes for this increment:

- **27 actual product/UI/API browser groups passed**, with all **550 bound
  runtime/configuration/proof files** matching the final worktree. These cover
  authentic Courtyard and profile images, exact document/stack/task/file
  destinations, source navigation, keyboard reading continuity after real
  material refresh, more than twelve messages, and an eight-line composer with
  left/right material and focus mode. The existing native message, document,
  task, workflow/worker, upload, appearance, provider-boundary and visitor/reset
  checks remain. Swedish at 320 px edits and reopens actual saved document text
  while retaining the conversation draft and a strict visual source anchor.
  No unexpected external requests, retained credential accesses, IndexedDB
  operations, service-worker registrations, page errors or rate-limit failures
  occurred.
  The automation waited **six seconds between completed groups**, using the
  unchanged public demo limits and normal product API.
- **11 normal-product browser groups passed**, retaining all ten existing
  continuity, tool-memory, account/workspace, pending-document and Swedish
  320 px groups. The new group uses the actual account file chooser and canvas
  crop, then the real profile-photo API. Account, header, message and member
  portraits display the same authenticated bytes; replacement changes them,
  and deletion returns the API to 404 with initials in the UI. All **20 bound
  frontend/backend/proof source hashes** matched at completion; no browser
  page errors occurred.
- **Five built-application handoff groups passed**, preserving the real frame,
  reload, documentation, English/Swedish and 1440/390/320 px checks. The loaded
  native-product screenshot's button opens the configured product iframe.
  Application and trusted-embed production builds passed, with **325 source
  hashes** matching and **76 built-artifact hashes** recorded. Expected iframe
  navigation was inspected and aborted before creating a sample account;
  no unexpected external/account requests or page errors occurred.
- **22 native profile-photo integration cases passed**, including current
  membership, guest/block restrictions, account deletion during storage I/O,
  bounded PNG decoding/metadata removal and durable erasure. These are part of
  **117 final runtime, operations, demo-boundary and avatar cases**. The native
  worker drained a pending photo erasure and reported its fifteenth operation
  group through the actual queue/worker contract. **Ten client read
  cases passed** for pending-request sharing, independent cancellation,
  account/workspace isolation and late transport completion after invalidation.
- **Eight material-reader and actual editor/click-guard cases passed**. They
  cover native/encrypted boundaries, same-channel target validation, stale
  scope rejection, bounded document/project reading and one-time deep-link
  consumption that preserves a newly created page. TypeScript also passed.
- The complete frontend Node run passed **434 cases**.
- The original **2,335-case backend cohort has passing file-group coverage**:
  553 + 464 + 667 + 651. The affected 31-file group was rerun exhaustively in
  four private groups of 154 + 209 + 157 + 131 cases after its worker contract
  fixes. The final 117 targeted cases above overlap this cohort and include
  three later regressions; they are not 117 additional distinct cases.
- All **1,320 relative Markdown file links** across the README and documentation
  resolved in the final worktree.

The normal-product browser fixture used its own accounts, API/Vite processes,
database, image files and keys, then removed them. Earlier sections retain the
results for their original source state; their totals are not additional runs
of this design increment. Live provider, Matrix, public deployment and physical
mobile acceptance remain separate scopes.

## Product demo parity — 2026-10-23

**Historical source:** the following 16-group/533-file evidence describes the
preceding parity increment, before the shared product room validated above.

The public demo now mounts the actual `Chat`, `WorkHub`, editors, providers and
settings against the normal backend and native integration worker. The separate
browser-only `Demo*` feature components, reducers and template AI responses were
removed. Their old unit/55-group showroom proofs below are historical; they are
not counted as coverage of the new implementation. [Product demo contract](PRODUCT_DEMO.md)
describes isolated sample accounts, expiry/reset and disabled external services.

- **16 actual UI/API browser groups passed**, with **533 runtime source hashes**
  stable through the run: native send/edit/reaction/thread, channel CAS,
  Yjs editing/comments, stack move/Undo/derived discussion/source navigation,
  task checklist/status, manual workflow → approval → real worker-created task,
  exact file upload/download, personal appearance, actual disabled Copilot and
  Microsoft/Google setup guides, visitor/reset isolation and Swedish at 320 px.
  Retained credential access, IndexedDB calls, service-worker registrations,
  third-party requests and page errors were all zero in those interactions.
- **19 new backend core integration cases passed**, plus **34 existing release
  and configuration regressions**: exact JWT/ActiveSession expiry, separate
  visits, files, reset/revocation, capacity concurrency and FK-safe cleanup with
  over100 threaded messages. Ordinary product startup leaves `/demo/session`
  absent. A separate actual workflow/worker cycle created exactly one task.
- **93 boundary/worker/operation cases passed**, including default product
  behavior, native idempotent outbox, a real scheduled-message worker dispatch,
  changed/expired/revoked actors, body/budget limits, disabled egress and
  cross-site bootstrap denial. Independent review found a noncanonical
  `/socket.io/*` dispatch bypass; **three focused actual Engine.IO cases passed**
  after rejecting suffixes before dispatch while retaining normal handshakes.
- Client/session/cache tests and TypeScript passed. The actual-module retained
  pointer regression verifies that sample auth changes do not open or clear
  standalone IndexedDB; ordinary and trusted-embed behavior is unchanged.
- The final broad run passed **2,314 backend cases** across four exhaustive file
  shards (657 + 486 + 658 + 513). The three focused Engine.IO checks above also
  passed after the final dispatch regression was added. **416 frontend Node
  cases**, the application/embed/demo builds, **10 normal-product conversation
  groups**, **five application handoff groups** and **seven website groups**
  passed. Website/handoff checks cover the frame and documentation; the 16
  actual product groups above exercise its contents against a real backend.
- Real product tests also exposed two shared usability defects: the hover-only
  message toolbar could become unclickable, and a late initial project load could
  erase a new editor. The shared controls were corrected and exercised through
  the same demo/product components.

Publication and exact public bytes require their separate deployment evidence.
These checks do not establish live customer-provider, Matrix or physical mobile
acceptance. Public samples intentionally use the product's actual disabled or
unconfigured provider states, with no fake model answers or connected accounts.

## Implementation and documentation audit — 2026-10-23

The [current feature audit](FEATURE_AUDIT.md) maps the original requirements and
subsequent product decisions to actual UI/API/storage and tests. Its domain
ledgers distinguish implemented bounded flows, customer configuration/external
acceptance, local demo examples and missing code. [Self-hosting](SELF_HOSTING.md)
describes local computer/LAN/VPN installation: accounts belong to that
installation, with no required central FreeCollab service. The public static
Railway showroom is a separate delivery.

The audit added real native document stacks, generation/CAS-protected moves and
Undo, reviewed document relations, message-source references, placement-derived
stack discussion and read-only selected-page stack AI. It also added revisioned
native/Matrix channel metadata and fixed stale list rollback, custom presence,
Socket.IO REST-prefix normalization, roots-only timelines with paged thread
history, fresh authorization after channel locks, app-AI reviewed source/provider
binding, future-only recap consent, complete fourteen-group worker diagnostics
and Unicode/UTF-8 password checks. The anonymous server AI demo was retired.
New in-app help links the actual SDK and production feed contracts.

Executed independent scopes, with overlapping coverage rather than an additive
grand total:

- The unchanged `c2762404` baseline passed **2,141 backend cases** across four
  exhaustive file shards. A preceding sequential run was interrupted and is
  excluded from that pass claim. Tests used isolated synthetic SQLite, storage,
  signing/encryption material and private provider fixtures; existing services
  and operator configuration were not used as fixtures.
- **27 SDK release checks** passed, including the actual standalone package
  artifact/declaration checks; tarball and integrity manifest were verified.
  The real packed SDK browser proof passed **26 checks** across its native
  modules and React/customer-scoped sessions. No npm publication occurred.
- **15 real Copilot browser groups** passed, preserving the existing request,
  source, consent, draft and approval checks. The new group created actual Yjs
  documents and a native stack, deselected a page, reviewed the selected page
  and sent only its verified body to a private HTTPS/SSE fixture. Unselected
  pages/comments/messages were absent and no shared content was written.
  The report binds 26 current source hashes.
- **Nine real app-platform browser groups** passed, preserving the previous six
  and checking source/provider review, explicit approvals and future-only recap
  cursors. Customer credentials and actual model providers were not contacted.
- **Seven real channel-metadata browser groups** passed: discover/save/cancel,
  reload persistence, stale CAS draft retention, member/admin entry and Swedish
  focus/layout at 320 px. Actual encrypted reader/writer tests separately cover
  authoritative event and account-generation handling; this native browser
  harness is not new live Matrix acceptance.
- **Seven real communication browser groups** passed against the full app,
  API and Socket.IO. They created 107 messages including 105 thread replies,
  retained roots and scroll position, paged all replies, isolated drafts and
  stale responses, opened exact search sources, reviewed parent/reply stack
  sources without saving, and saved/reloaded/cleared real custom status with
  acknowledged realtime transport. The report binds 21 current sources.
- Independent source review found no remaining concrete security defect in the
  reviewed channel metadata, message references, comment filtering, retention
  joins and E2EE conversion fences. A separate sweep checked **875 relative
  audit links**, all resolving. This is internal code review, not independent
  security certification.

- The final implementation passed **2,238 backend cases** in four exhaustive
  file shards: 657 + 509 + 601 + 471. Each shard exited successfully; no skipped
  shard or interrupted run is included. The unchanged baseline is a separate
  comparison, not additional current-source coverage.
- **480 frontend Node cases** passed, with zero failures/skips. TypeScript and
  production application/trusted-embed builds passed. The final static website
  build and **eleven actual website browser groups** passed, retaining all ten
  existing groups and adding English/Swedish product help at 1440/390/320 px.
  Thirteen guide links, installation-local accounts/private-network operation,
  no required central cloud account, actual SDK/feed/comment examples and paged
  thread-history examples were checked. All 96 bound source hashes matched.
- **15 document-organization browser groups** passed with 19 current source
  hashes. Actual drag/keyboard move, persisted placement, guarded Undo after text
  editing, concurrent changes, reviewed relations, in-flight comment drafts,
  derived discussion/source focus/archive filtering, and real main/thread
  message → review → save → original-source navigation were exercised. Both
  main and thread drafts survived. Swedish 320 px remained usable.

These are local implementation proofs; publication and actual deployed bytes
require separate evidence. No customer application/API/database deployment is
implied by a successful static website build.
The detailed ledgers describe actual limits: no general AI run/step engine,
model routing/spending ledger, autonomous semantic pairing or Matrix document
organization is implied by the implemented flows. Customer OAuth/SSO, actual
Office, physical mobile behavior, app-store distribution and off-host recovery
remain separate acceptance work.

## Conversation continuity — 2026-10-22

The demo opens its existing brief and decision in context beside the mounted
conversation. Mobile returns to the original source with its draft, selection,
channel and reading position retained, including detours through settings or
another channel. The agreed next step is also actionable beside the discussion.
The authenticated client's generic Work action resumes the last selected tool
within the current account session, workspace and channel. Explicit resource
links retain precedence; closed tools are unmounted and existing unsaved-change,
focus and secure-channel eligibility guards remain.

Executed local validation has overlapping scopes:

- **300 frontend Node cases passed**, including six new bounded navigation-memory
  cases for scope, credential/encryption changes, explicit clearing and eviction.
  TypeScript and production application/trusted-embed, website and widget builds
  passed.
- **Nine actual App/API/SQLite/Vite browser groups passed**: stable conversation
  DOM/draft/caret/reading position, return focus, channel-specific tool choices,
  explicit resource navigation, workspace/account/credential isolation, reload
  reset, real pending-document close/tab guards and Swedish at 320 px.
- **23 actual demo Chromium groups passed**, retaining all previous 14 and adding
  nine continuity groups. These cover the same mounted composer, brief/decision
  drafts, the compact task's shared identity, channel retention, Escape/focus,
  own-message following and Swedish 320/390 px return positions, including
  context-to-settings and context-to-channel detours. API/provider isolation,
  routing, reset, personal appearance and literal user content remain checked.
  The complete product CI found a scroll-event race: opening material before the
  queued scroll event could restore an older following position. Navigation now
  snapshots reading synchronously before changing surfaces. The new regression
  sets the actual scroll position and clicks the actual brief control in the same
  JavaScript turn: it fails on the preceding source, passes with the fix and
  verifies unchanged transcript identity and dimensions, draft and selection.
  Original keyboard checks and one-pixel reading assertions remain unchanged.
- The new self-contained downloadable preview passed **28 actual Chromium groups
  on its exact ZIP-extracted HTML**, served by an isolated loopback HTTP fixture.
  All previous 27 groups remain, with the same-turn reading regression added.
  It made no external/API requests or workers and retained no storage entries.
  A separate supplement installed method counters before app startup: its named
  interactions made zero observed Storage/IndexedDB/service-worker registration
  calls, and real operations on an isolated control page verified the Storage
  and IndexedDB counters. Uninstrumented storage surfaces are outside that scope.
  The managed browser blocks `file://`, so direct file-URL execution is not
  claimed by this increment's preview proof.
- Separate read-only source reviews cleared the bounded tool memory, explicit
  navigation precedence and unchanged authority/unsaved/focus guards. A discovered
  mobile detour return-position gap was fixed and included in the browser checks.

All browser fixtures used their own source snapshots, accounts, databases and
ephemeral keys; their own services and contexts were removed. No live customer
identity/provider acceptance, new Docker installation, release or Railway
deployment is claimed by this increment. The published commit's own GitHub Actions
run remains authoritative for its complete PostgreSQL/product and real-crypto CI.

## Language preference acknowledgement fixture — 2026-10-22

Language selection updates the catalog immediately while its real preference PUT
is pending. The browser proof could read the old server preference before that
write completed. It now waits up to ten seconds for the existing saved-status
message before the same authoritative server GET. Every immediate composer DOM,
draft, selection, user-content and HTML-language assertion remains before the
wait. No product behavior changed.

A private gate before the real PUT handler deterministically reproduced the
original old-value assertion failure. Releasing that same request passed the
corrected proof; withholding it produced the expected bounded saved-status
timeout and retained the old server value. Both the complete canonical language
script and the controlled successful run passed **all 16 groups**, overlapping
scopes. These include persistence without local cache, stale responses, separate
account queues, English/Swedish and mobile controls. The final published commit's
own CI records its complete workflow status.

## In-flight file cancellation fixture — 2026-10-22

The file queue permits cancellation before an upload is dispatched. The browser
fixture could select that valid state, then assume it had captured two upload
requests when checking a retry. It now waits for its first actually intercepted
POST before cancelling and requires exactly two nonempty, identical idempotency
keys. No product, authorization or delivery behavior changed.

A controlled real-ChannelFiles proof reproduced the old queued-cancel assumption
and its indexing failure, then verified the corrected in-flight scenario. A
zero-arrival negative control produced a bounded Playwright timeout. The complete
canonical file browser script also passed **all seven groups** without private
code injections: exact downloads, immutable versions, image/PDF previews,
cancellation/retry, lost-acknowledgement deduplication, search, mobile layout and
logout cleanup. Separate read-only review checked the actual request gating and
preserved assertions. The earlier candidate's four backend shards, frontend,
cryptography and Android/iOS jobs passed; its product workflow stopped at this
fixture. The final commit's own CI remains the complete workflow record.

## SCIM simultaneous first-load follow-up — 2026-10-22

Opening SCIM settings fetches status and mappings concurrently. Both can create
the first inactive directory, but the mappings read lacked the workspace lock
already used by status and the three administrative write paths. The complete
product workflow on `ac1b3a70fee6bf7fa8cbb8bbf65e8cc38e79b429` exposed a duplicate
primary-key insert. The mappings read now takes that same lock before get/create;
authorization, reauthentication, token defaults and commit boundaries are unchanged.

A deterministic regression uses two real HTTP clients and separate SQL sessions.
It fails on the old application with the actual SQLite uniqueness conflict and
passes with the one-line fix. Both reads return the same single inactive
directory; existing credentials, workspace membership and audit state remain
unchanged. The complete SCIM, workforce-security and workforce-onboarding test
files passed **46/46 cases**, without skips or failures. The unchanged
connection-setup browser proof then passed **all 17
groups**, including SCIM, current-authority/reauthentication/revocation controls,
English/Swedish and 320/390 px, with no external provider calls. Separate read-only
review verified the final diff and the before/after/browser evidence.

The application client, website and deployment configuration are unchanged from
the Open Studio candidate below. Its offline preview therefore uses the same
reviewed source files. The Docker installation rehearsal below ran that candidate;
this follow-up changes only SCIM initialization and its regression test. The
earlier candidate passed all four backend CI shards, frontend, real cryptography
and Android/iOS compatibility; its product workflow failed at the SCIM race.
The final published commit's own GitHub Actions run records its complete CI status.
No release or public deployment is claimed.

## Open Studio first conversation and launch rehearsal — 2026-10-22

This untagged `design/open-studio` increment at
`ac1b3a70fee6bf7fa8cbb8bbf65e8cc38e79b429` retains the approved open-room
design and makes its first team conversation explicit. The public demo connects
a conversation to its same editable brief, a decision and one assigned next step.
Microsoft and Google cards open their own preparation guide directly. Customer
credentials and consent remain in each customer's installation.

| Area | Executed evidence |
| --- | --- |
| Client | **294 frontend Node cases passed**, including the 15 onboarding and 10 demo route/reducer cases. TypeScript and production application/trusted-embed and independent website builds passed. Four i18n cases passed again after aligning the built English/Swedish installation instructions with Node 24 LTS and the hash-locked development environment. |
| First conversation | **12 actual two-user browser groups passed**: account entry, accepted workspace invitation without channel access, separate accepted channel invitation, focused composer, bidirectional messages, outsider denials and encrypted-at-rest native storage. **Seven additional independent browser/API groups** cover live channel-admin demotion, issued-link/unsaved-change guards, lost-authority link scrubbing, revoked invitations and outsider access. English, Swedish, keyboard operation and 320 px were exercised. These native channels are server-readable, not end-to-end encrypted. |
| Public story and isolation | **14 demo browser groups passed** across the application and website entries. An independent run passed **16 production-browser groups**, including the actual English/Swedish installation page in both entries. Checks cover provider destinations/history, the shared brief, decision revisions without duplicate tasks, reset/reload, keyboard use and 320/390 px. The independent website ignored seeded account material and made no account/provider requests or worker/private-content storage operations. The full application's demo retains the application's normal account/mobile startup; its demo content and actions remain local. |
| Downloadable preview | **22 actual offline Chromium groups**, an additional Swedish 320 px check and separate archive/source review passed. The preview uses the actual demo, styles, fonts and artwork with memory-only example content. Its HTML and ZIP are bound to source hashes; it is not a live account, provider or encryption demonstration. |
| Fresh self-hosted installation | The real Compose/Docker installation used fresh configuration, PostgreSQL, API, default integration worker and production Nginx frontend. Initializer permissions/refusal to overwrite, first-operator bootstrap/refusal to bootstrap twice, explicit invitations, messages, unauthorized access and hash-checked cross-user file downloads passed. Restart retained accounts, roles, history and file bytes. **Five production-app browser groups and six independent-website browser groups passed** on the final Docker images. A separate disposable Matrix profile passed health, registration, login, authenticated identity and logout; this control-plane check alone is not a cryptographic proof. |

The recorded scopes overlap and must not be added as a unique suite total.
All fixtures used new accounts, keys and data; the retained pilot's four services
and start times were preserved. The rehearsal used loopback HTTP and does not
establish public TLS/WSS, customer Microsoft/Google acceptance, backup service
readiness or production capacity. The migration runbook now stops all running
Compose writers before content-key migration and resumes only that prior list.

The backend application source is unchanged by this increment. At its parent
`8777e015610b6de390a445e17b022494241ee85e`, all four backend CI shards, frontend,
the product/PostgreSQL browser job and native Android/iOS compatibility passed.
The real encryption job exposed a Connect test navigation race during correctly
enforced recipient logout. The scoped test correction retains every API, socket,
Matrix, signed-out and private-DOM assertion; unrelated refresh timeouts still
fail. A separate actual Chromium reproduction and three negative controls passed.
The corrected harness then passed **all 13 actual Connect groups** with two
organizations, the packed SDK, real embedded Rust-crypto clients and a fresh
Matrix homeserver, including bidirectional encrypted delivery and recipient/
connection revocation. All source and built-file bindings remained unchanged.
The workflow for each final published commit remains the authority for that
commit's CI status. No release, npm publication or public Railway deployment is
claimed by this record.

## 0.13 mobile calls, external calendars and encrypted shared work — 2026-10-22

[PRODUCT_013.md](PRODUCT_013.md) describes the supported features and boundaries.
All fixtures used disposable accounts, databases and independently generated keys.
The retained pilot, its independent keys and the original four services were
preserved. Railway and the public website were not deployed.

The counts below are executed checks with overlapping regression scopes; they
must not be added as a unique complete-suite total. The repository's
[GitHub Actions](https://github.com/freecollab-eu/freecollab/actions/workflows/ci.yml)
records the status for each published source commit.

| Area | Executed evidence |
| --- | --- |
| Backend execution and CI | **425 cases passed**, with no failures/skips, in one independently isolated shard of the current source. Six selector checks and four complete collection runs prove all **2,038 cases in 114 files** are assigned exactly once across four CI jobs; collection is separate from execution. The previous published 0.12 commit passed **1,914 backend cases** in GitHub CI. Unique coverage files and separate PostgreSQL services keep shard results independent. |
| Client and SDK | **274 frontend Node cases** and **26 SDK cases** passed. TypeScript and main/trusted-embed, widget and independent website production builds passed. |
| Native calling | **53 combined backend cases**, **11 actual App browser groups**, actual JVM guard/callback/stale-intent checks and **10 real Nginx/TLS gateway groups** passed. Android 0.13/build 1300 compiled with the actual LiveKit SDK and 18 native SDK libraries. The real iOS simulator project compiled successfully with Xcode 16.4 on GitHub; five platform-policy cases passed there. Windows and macOS unsigned desktop artifacts also built. These are not physical-device, customer-signing or live push-delivery acceptance results. |
| Calendar delivery | **169 backend/provider/sync cases**, **19 Node cases** and **9 actual Chromium/worker/trusted-HTTPS groups** passed. They cover supported recurring Google/Graph series, moved/cancelled exceptions, deliberate invitations, personal provider RSVP, organizer cancellation, source loss before each wire request, partial-effect uncertainty, actual password/MFA recovery confirmation, Busy/title ICS privacy/revocation and Swedish at 320 px. No live customer tenant or recipient email-delivery certification is claimed. |
| Slack migration | **44 backend cases**, parser/checkpoint/receipt Node regressions and **eight actual Rust/Chromium groups** passed. A genuinely dropped accepted response resumes with exactly two unique messages, one reply and the correct importer/peer thread count. Historical reactions/edits, 15 KiB chunked details, peer-decrypted attachment, encrypted IndexedDB checkpoint, literal Chat disclosure, Swedish at 320 px and removal/new-event denials passed. Fresh sign-ins and ambiguous Matrix devices cannot adopt an old reservation; known accepted receipts are preserved before ordinary pause. Cached plaintext is not a remote-erasure guarantee. |
| Encrypted shared work | **41 backend cases**, **34 focused client/i18n cases** and **18 actual groups with two independent Synapse homeservers and Rust crypto** passed. These include both-side tasks/calendar/documents, concurrent Yjs, large encrypted fragments, competing task heads and explicit merge, malformed-history isolation, read-only incomplete ancestry, actual React/WorkHub/mobile navigation, opaque wire/SQL indexes and current participant/bilateral revocation. A later two-option date-presentation change passed three real React/Intl checks in America/Los_Angeles; all other crypto/protocol hashes remain identical. |
| Realtime under contention | **270 focused cases** passed. Seven new real one-connection-pool regressions fail on pinned public 0.12 and pass with the fix. A disposable SQLite load ran **400 reads, 40 writes, 16 actual WebSockets and 640 unique live deliveries** at 20 in-flight REST requests. Exact committed history/file bytes, outsider 403 and post-logout REST 401/live-delivery denial passed. The measured 27.688 seconds and latency percentiles describe that fixture under concurrent test work, not production capacity. No pool enlargement or authorization cache was used. |
| Distribution and static demo | Nine release-boundary checks, five-workflow actionlint, workflow shell/Python checks and actual Compose digest-pin merges passed. Both app Nginx configurations passed 20 protected raw capability-path variants, with negative controls reproducing the old log leak and unchanged HTTP statuses. **Six real Nginx/static-website browser groups** verify account/socket/embed/native/service-worker404 fences, CSP, local demo reload erasure, untouched account storage, no provider/API requests and English/Swedish at 390/320 px with bundled Manrope. This local website proof mounts the built artifact into actual Nginx; the CI job separately builds and tests the complete website Dockerfile. |
| Separate review and dependencies | Separate internal checks passed **14 backend/owned-TLS cases**, **16 retry/bootstrap cases**, **two actual JVM checks** and **six shard checks**; these overlap feature results. Actual installed Matrix SDK state-machine checks use explicitly synthetic crypto/HTTP. Final source review covers original-session fences, per-request provider effects, ZIP preflight allocation, device/intent incarnations and encrypted retry epochs. Production npm audits reported zero advisories for frontend, SDK, desktop and mobile; Kotlin/Swift, container OS packages and independent security certification are outside those audits. |

The canonical SDK artifact is `freecollab-components-0.13.0.tgz`, SHA-256
`a7209bc812ae65928d8df8226e4d7fc4e15a29d325a22731776b908cb7fffcc1`.
It targets matching 0.13.x APIs and trusted clients. npm publication requires the
maintainer's registry identity and is separate from building this exact archive.
The final Android debug APK SHA-256 is
`b967241827b1a8011304b1615f05236f537d5cc5af2b377563ee85a739e98c7e`.
It contains no customer's Firebase configuration or release-signing identity.

Reproduce targeted checks using the locked environments and the feature guides:
[NATIVE_CALLING.md](NATIVE_CALLING.md), [CALENDAR_DELIVERY.md](CALENDAR_DELIVERY.md),
[FEDERATED_WORK.md](FEDERATED_WORK.md), [SLACK_IMPORT.md](SLACK_IMPORT.md),
[SCALING.md](SCALING.md) and [DISTRIBUTION.md](DISTRIBUTION.md).
Private synthetic reports under the environment's `.freecollab/proof-results/`
are excluded from source publication. Signing/store identities, physical phones,
live customer providers, public deployment, representative capacity testing and
an external security audit remain distinct acceptance steps.

## 0.12 personal work, portable history and installation boundaries — 2026-10-21

[PRODUCT_012.md](PRODUCT_012.md) describes the eight implemented areas and their limits.
All API, browser, transport and native fixtures used disposable data and independently
generated keys. The original pilot database, retained keys and four services were
preserved. Railway and the public website were not deployed.

| Area | Executed evidence |
| --- | --- |
| Complete backend baseline | **1,880 passed; 3 stale worker-expectation failures** in the 1,883-case complete run. Those tests were collected before the OAuth-cleanup batch expectation was updated during parallel development. The final on-disk worker suite then passed **all 13 cases**. Later focused feature/regression runs below cover the final changes; this is not a second complete run of the final 1,912-case collection. |
| Client and SDK | **221 frontend Node cases**, full TypeScript, main/trusted-embed and widget production builds passed. **26 SDK cases** passed, including a standalone packed consumer resolving every entry/declaration and typed recurrence fields. |
| Personal working day | **8 API cases and 7 actual full-App browser groups** passed: current cross-workspace aggregation, exact project/task navigation, revision-based completion, reminder snooze/completion, private saved completion, source refresh, Swedish and actual fonts at 320 px. |
| Calendar | **37 focused native/legacy/CalDAV API cases**, **15 Node cases** and **5 actual browser groups** passed, including two-browser invitations/RSVP, recurrence exceptions, shared-time bounds and narrow layout. New unused fields are omitted from standalone payloads to preserve previous sync fingerprints. |
| Slack import and library | **37 final API cases** passed, including unsafe ZIPs, mapping authority, SQL/blob rollback, immediate private-preview erasure under enforced foreign keys, retained shared provenance, immutable versions, exact package grants and publisher lock ordering. **10 actual UI groups** passed: multipart preview/import, historical labels/threads/files, deliberately held old catalog reads, public review/install, handled review-queue failure, secret leave guard, customer palettes/fonts in both new and existing Apps, Swedish at 320 px. |
| Personal OAuth and apps | **88 combined OAuth/search/apps/audit cases plus 2 additional real HTTPS calendar-write cases** passed. Google and Microsoft code exchange, refresh, revocation and Calendar create used independently verified TLS/SNI through a synthetic CONNECT proxy. Immutable reviewed consent IDs prevent an old queued action executing under a replacement provider account. **9 actual App browser groups** passed; browser consent responses are explicitly synthetic, with separate backend wire-protocol evidence. Native/embedded consent is directed to the normal browser without weakening cookie or origin boundaries. |
| Native push | **99 combined native/Web Push/native-client/worker cases**, **7 actual App browser groups**, frontend/i18n/type checks and an actual application WebView-guard JVM test passed. Actual APNs HTTP/2/TLS/CONNECT and independently verified provider signatures are covered. Scoped device management, deletion during provider exchange, expired-source reenrollment, enforced-FK account erasure and concurrent closure/registration are exercised. The browser OS bridge/provider are synthetic; live Apple/Firebase delivery and physical devices are separate acceptance checks. |
| Encrypted offline library | **10 new actual production-browser groups**, **11 existing mobile/offline groups**, and **23 focused Node cases** passed. Real service worker, IndexedDB, Web Crypto, binary download, offline reload, encrypted attachment persistence, lost-acknowledgement upload retry, known membership revocation, public-only CacheStorage, logout erasure and 320 px are covered. Revocation cannot be discovered while disconnected; explicitly exported OS copies remain outside local app erasure. |
| Separate installations | **46 backend cases**, **26 focused client cases**, TypeScript and **8 actual two-homeserver browser groups** passed. Two independent pinned Synapse homeservers used verified TLS federation; actual Rust devices verified fingerprints and exchanged/decrypted Megolm messages and encrypted attachments. Current remote removal and either-side revoke passed. Ten critical runtime files were hashed before startup and unchanged after completion. This is Matrix chat/media federation, not replication of native task/calendar/document index tables or a live customer SSO rollout. |
| Trusted embedding and local Connect | The 0.12 SDK payload passed **21 actual trusted Embed/OIDC/Matrix groups** and **13 same-installation two-organization Connect groups**. The current **468 runtime source files** still match the final Connect proof. The later canonical package changed only file modes: all 59 member payloads remain identical; normal public 0644 permissions and byte-identical archives under umask 077/022 are verified. |
| Previous CI browser failures | Updated stale delivery-endpoint and narrow-screen tool-menu expectations; the actual Elite flow passed **6 groups**, retaining durable-queue and exactly-once delivery assertions. Shared-document proofs passed **12 groups**, including two real Rust devices, encrypted chunks/snapshots, project/task/workflow history and opaque API/database/wire checks. A fixture-only bounded retry handles declared Matrix 429 responses with the exact same transaction and ciphertext; an injected 429 and three observed retries passed. This does not change production-client throttling behavior. Explicit disposable-homeserver settings replace retained-secret discovery. Both harness patches received independent review; the new GitHub run remains separate. |
| Native artifacts and edge routing | Android 0.12/build 1200 debug APK compiled and was inspected for the guarded application WebView, Firebase service and disabled pre-consent auto initialization. Actual Nginx 1.28 loaded both frontend and federation-edge configurations; verified HTTPS callback/Office requests passed, no-referrer was present and synthetic capability/query markers were absent from proxy logs. No iOS compilation, platform signing, stores, CallKit or background media service is claimed. |
| Independent review and dependencies | Independent review reproduced and verified fixes for Android cross-frame fallback, scoped device controls, reviewed OAuth-consent binding, account-erasure races and private credential cleanup. Its final **14 targeted backend checks and one JVM check** passed; these overlap the feature suites. PyPI-backed pip-audit 2.10.1 reported **zero known vulnerabilities across 76 locked Python runtime packages**, after upgrading h2 to 4.4.1. Production npm audits for frontend, SDK, desktop and mobile reported zero advisories. These exclude container OS packages and are not certification or load evidence. |

The canonical SDK artifact is `freecollab-components-0.12.0.tgz`, SHA-256
`2aaf40a5bcfb4b0a184ec88f92c1a1711f5a6792daf3d343b0203ad10380b91d`.
It targets matching 0.12.x servers/trusted clients. npm publication was not performed.
The original browser-tested archive had SHA-256
`d74d7978c20681e51c0e2bfa811c648fd9127702787a542a5d83b31d8e2df4af`;
the canonicalization proof compares all 59 uncompressed member payloads exactly.

The Android debug artifact SHA-256 is
`afd59c571809725aee558d540dcf070361211ebfc1b0750ff5a48923771498f4`.
It contains no customer's Firebase configuration or release-signing identity.

Reproduce API checks with the locked backend environment and `python -m pytest tests/`.
The new browser harnesses are `my-day-browser.py`, `calendar-planning-browser.py`,
`import-library-browser.py`, `personal-oauth-browser.py`, `native-push-browser.py`,
`offline-library-browser.py` and `connect-federation-browser.py` under `frontend/tests/`.
They own and remove their services; the final federation and SDK proofs require the
pinned cached Synapse image/Docker and installed Chromium. See each feature guide
for targeted commands and operator prerequisites. Reports under the environment's
private `.freecollab/proof-results/` are deliberately excluded from source publication.

## 0.11 daily collaboration, integrations and infrastructure — 2026-10-21

[PRODUCT_011.md](PRODUCT_011.md) records the implemented entry points and supported
boundaries. [INFRASTRUCTURE.md](INFRASTRUCTURE.md) explains the recommended
customer-controlled installation. Provider applications, identity, keys and
infrastructure remain customer-owned.

| Area | Executed evidence |
| --- | --- |
| Backend baseline | The complete **1,708-case** regression run produced **1,707 passes** and one stale test still patching the removed `aiofiles` storage hook. The test now injects a real partial-write failure into the current writer; **48 file/version/security/S3 cases passed** after that correction. Final integration changes passed **60 current-source Connect/Embed/presence/SSO/Valkey cases** and **17 offboarding cases**, following earlier overlapping 138-case identity/Matrix and 101-case lifecycle runs. This does not claim a second complete-suite run. |
| Client and package | Final TypeScript, main/trusted-embed Vite and widget builds passed; **193 frontend Node cases** and **26 SDK cases** passed. Normal frontend installation scripts passed with the documented Node-only CUDA download disabled. The dependency-only patches preserved all **1,262 browser Transformers/ONNX files** and **78 main/embed output files** byte-for-byte; subsequent sidebar, file-race and secure-room bootstrap fixes received a fresh full client build. |
| Existing product UI | **13 focused sidebar checks** and **all 7 pilot groups passed**. Create channel, footer and keyboard controls remain reachable at 1280×720, short desktop, 390px and 320px. The pilot exercises real signup/invitation, realtime, an explicit rejected-send draft/retry, file access, current membership loss and logout. Deliberately held real initial file-list responses cannot overwrite a later acknowledged upload or deletion. |
| Daily chat | **8 actual full-App browser groups passed**: private three-person group creation, durable worker delivery into connected chat without Valkey, encrypted scheduled payload/no stored JWT, generic personal reminder navigation through current authorization, private saved-message completion, AES-GCM device scheduling without early send, recovery of a deliberately missed live message through the bounded native cursor, and Swedish320px without overflow or page errors. Independent backend review also exercised source authority changes, concurrent workers and private reminder access. |
| Shared runtime | **10 actual two-API/TLS-Valkey groups passed**, including bidirectional message/typing/mention delivery, per-device presence leases, workspace/private notification fences, separate-worker publication across repeated event loops, source/provider/member revocation, no bearer JWT in broker envelopes, shared atomic auth limits and fail-closed outage behavior. This is functional integration evidence, not a capacity/load benchmark. |
| Apps, search and governance | Actual browser proofs passed **6 apps**, **5 search** and **6 governance** groups. Signed commands/cards used a real private HTTPS receiver; search exercised real Yjs/task/checklist/feed/file-body extraction and reauthorized citations; policy/case/audit UI included current role and mobile checks. Private HTTPS protocol fixtures exercised S3 SigV4 and Vault Transit. These are synthetic customer-owned provider fixtures, not live Microsoft/Google/vendor tenant acceptance. |
| Shared channels across organizations | **13 actual browser/API/Rust-Matrix groups passed** with two separately managed organizations on one installation, both requiring customer SSO. Bilateral consent, explicit own-organization seats, first recipient-room join, trusted recipient embedding, bidirectional encrypted send/decrypt, no native plaintext fallback and both recipient/connection revocation were exercised. This proof uses fixture-signed workspace SSO sessions and independent provider registrations; actual HTTPS OIDC popup transport is covered separately by the trusted-embed proof. Cross-installation federation is not enabled. |
| Offboarding and authority | Final lifecycle checks cover permanent grant/seat removal, reinvitation requiring a fresh grant, inactive/orphan directory identities and remote Matrix seats, tenant-safe admin handovers, ordinary SCIM successor promotion under required SSO and remote-failure rollback. Native governed actors remain denied; independent grants survive unrelated group unlink. Additive Embed Matrix markers fail closed when child metadata or the independent marker is lost/mismatched; an existing valid device can refresh its marker through signed `/secure/session`. Actual governed Socket.IO handshake completed in 0.04 seconds after correcting an unbound presence-visibility filter; each recipient still receives current-token directional checks. |
| Clips and meetings | **9 actual browser groups passed** with real microphone/camera/screen recording, decoding/playback, pinned local Whisper ONNX inference and seven LiveKit devices exchanging real media. After the final participant-authority/fairness fixes, the real seven-device/clip proof passed again with **8 groups**; the unchanged ASR artifacts remained byte-identical. **19 targeted security regressions** covered source authority, fair revocation scanning and private snapshot cleanup, including independent review. SFU transport is not Matrix E2EE; there is no automatic meeting recording or live transcription claim. |
| Native clients | Actual Linux AppImage/Deb and Android debug APK builds completed. The corrected Linux packages passed ownership/read/traverse checks, exact tested ASAR comparison and actual launcher argument/no-implicit-sandbox-bypass checks. **14 actual packaged Electron browser groups** and **9 real React/API sign-in groups** passed, including explicit PKCE approval, current source logout, 320px and pre-consent media-device privacy. The container could not validate Chromium's OS sandbox; production retains it and does not automatically disable it. Windows/macOS installers and iOS compilation require their platform CI; signing, stores, physical-device acceptance and mobile FCM/APNs/background calls are not claimed. |
| Packed components and encryption | **26 packed native SDK** and **21 full trusted Embed/SSO/Matrix browser groups passed** against 0.11. Actual Rust-crypto devices verified fingerprints, exchanged encrypted messages and exercised lease renewal/revocation with the customer-owned synthetic HTTPS IdP. Parent DOM/storage isolation, exact origins, current scope, mobile layout and one-use PKCE remained covered. Native-client sources are rejected from creating nested embed sessions; focused regressions exercise the real PKCE rejection and source logout. |
| Dependency audit | `pip-audit` 2.10.1's PyPI service reported **zero known vulnerabilities across 73 locked Python runtime packages**. Production npm audits reported zero advisories for frontend, SDK, desktop and mobile. Transformers' unused Node branches received scoped sharp/global-agent patches; no browser runtime or ASR model version changed. These audits exclude container OS packages and are not security certification. |
| Built installation and cold recovery | **9 actual Compose/restore groups passed** with production settings, PostgreSQL, nonroot API, Nginx and the default integration worker. All **79 deployed frontend/widget files** matched the frozen bytes; real mjs/wasm/embed/widget/service-worker types and headers passed. JWT/logout, membership, scoped-admin and provider-revision fences passed. Scheduled delivery happened once; revoked native/SSO authority sent nothing. Normal Create channel submission passed at 1280×720 and native UI at320px. Cold restore retained ciphertext, file bytes, membership and revoked sessions; outbound workers stayed stopped until explicit resume. Corrupt archives and existing targets were rejected before target creation. |

The 0.11 SDK tarball is `freecollab-components-0.11.0.tgz`, SHA-256
`133b74ec3bc7fccf5e6c01eff931ce4e3dbb22046283f543e22be402bd477e5c`.
Compatibility targets matching 0.11.x backend/trusted-client deployments. npm
publication was not performed. Railway deployment remains paused.

Private synthetic reports are retained under `/workspace/.freecollab/proof-results/`:
`chat-productivity-browser.json`, `valkey-two-apis.json`,
`enterprise-search-browser.json`, `app-platform-browser.json`,
`enterprise-governance-browser.json`, `media-all-browser.json`,
`media-sfu-authority-browser.json`, `frontend-011-dependency-patch-proof.json` and
`sdk-release-0.11.0-validation.json`. UI regression records are
`sidebar-011-fixed-review.json` and `file-list-race-fixed-review.json`; the final
compiled artifact manifest is `frontend-011-final-artifacts.json`. The two-organization
record is `connect-browser-0.11.0.json`. The SDK record binds the exact tarball,
frozen frontend bytes and critical product source hashes. TLS hostname/CA
verification and session network policy were preserved. Existing pilot data,
keys and the four original Docker services were retained.

The final installation record is `production-compose-011.json`. To fit the VFS
storage budget, the exact hash-locked backend was flattened after matching its
runtime configuration and **15,165 filesystem entries** (excluding only
Docker-generated hosts/hostname/resolv.conf). The final frontend used the frozen
Node24 build in the normal Nginx final stage; its full Docker builder was not
rerun locally after a storage-guarded attempt. Source and restore ran sequentially,
and only their own resources were removed. The HTTP loopback UI origin differed
from configured production HTTPS, so its expected live-connection banner and HTTP
reconciliation were observed; this run does **not** verify public TLS/WSS.

CI creates the expected backend virtual environment, selects the installed
Playwright Chromium, skips unused Node CUDA downloads, retries transient Synapse
startup resets and includes both production Docker builds plus the shared-channel
proof. YAML and referenced commands were checked locally; a new GitHub-hosted
0.11 run is not claimed as passed by this record.

Live customer providers, internet media/TURN routing, platform signing/stores,
capacity/load acceptance and a complete external security audit remain separate.
The historical sections below record the source and deployment state at their
own execution time; their results are not substituted for this increment.

## 0.10 embedded product and expanded SDK — 2026-10-21

[The SDK guide](COMPONENT_SDK.md), [customer-owned embedding identity](EMBED_IDENTITY.md) and [release procedure](SDK_RELEASES.md) describe the implemented contract. The SDK has eleven native modules, Web Components and optional React wrappers. The trusted-origin adapter runs the actual product client, including Matrix encryption, device verification and recovery. Native inline components continue to reject encrypted channels.

| Area | Executed evidence |
| --- | --- |
| Backend | **165 targeted cases passed** across embedding identity, workspace SSO scope/unlock, sessions, secure messaging, Matrix proxy/device/revocation races, deployment kit and audit. This includes **56 embedding cases** covering exact origins, native-admin password/MFA, one-use S256 grants, concurrent exchange, source/application/provider revocation, timezone-independent expiry, same-bearer lease renewal and preserved authentication age/unlock. An expired authoritative lease rejects REST, renewal, joined sockets and a held Matrix-sync response even when the JWT signature/hard expiry remains valid. This is a targeted regression run, not a new complete backend-suite run. |
| Client and package | The full TypeScript/Vite build, including the fixed trusted `embed-entry.js`/CSS, passed. **164 frontend Node cases passed**, including existing account/security/language behavior and two new memory-session isolation cases. **26 SDK cases passed**: 20 native client/type/security checks, five sign-in/session-helper checks and one actual tarball consumer test. The final tarball was independently extracted: 15 framework-neutral entry imports and NodeNext declarations passed without React. |
| Native SDK browser | **26 actual packed-package Chromium groups passed**: task/project revisions, Yjs document create/edit/reopen, calendar deletion, decision revisions, meeting create/end/navigation, workflow approval/queued runs, chat edits/threads/reactions/bookmarks, inert file preview and bounded local search, plus account switching/logout/teardown, corruption fencing, Swedish320px and React StrictMode. Native product source hashes remain current; the SDK guide was subsequently updated for the final lease/distribution documentation. |
| Customer administrator UI | **10 actual UI/API browser groups passed** through Integrations → Embedding: exact-origin validation, create/copy channel-specific native/E2EE examples without bearer credentials, password clearing on success/failure, same-origin rejection, revision/disable invalidation of an existing child and unused grant, member/scoped-admin hiding and API403, and 320px without horizontal overflow. |
| Full trusted embed and real encryption | **21 actual packed-SDK Chromium groups passed** with independent host/trusted origins, production APIs, scoped managed users and the actual full client. A private HTTPS IdP exercised normal OIDC discovery/token/JWKS/callback and the PKCE popup. Two real Rust-crypto clients compared/verified device fingerprints through the UI, sent/read encrypted messages, and created recovery without exposing the key to the parent bridge. Wire assertions observed `m.room.encrypted`, not a native plaintext fallback. A private 20-second lease exercised automatic renewal with unchanged JWT, device, fingerprint and peer trust, followed by successful encrypted delivery; an unmounted lease expired with401. Exact CSP ancestry, wrong-source/nonce messages, parent DOM/storage isolation, standalone-session preservation, stable brand theme, acknowledged child-only logout, mobile layout and actual UI registration/disable were checked. The final run needed no stale-device revocation; it makes no claim that its conditional stale-device cleanup branch executed. |
| Deployment and publication | **8 actual Nginx checks passed** with the retained binary/libraries and private loopback upstream: exact public-host forwarding/privacy controls plus fixed embed JS/CSS MIME/no-cache delivery. Both workflow YAML files parsed and new Python harnesses compiled. CI installs the locked SDK in each relevant job and includes administrator and real encrypted-embedding proofs; GitHub Actions was not executed here. The reviewed tarball and SHA-256/SHA-512 manifest exist locally; npm publication was not performed (`ENEEDAUTH`). Railway deployment remains paused, and the new public GitHub repository has not been published from this environment. |

Private reports under `/workspace/.freecollab/proof-results/` are `component-sdk-browser.json`, `embed-admin-browser.json`, `embed-browser.json`, `embed-native-nginx.json` and `sdk-release-010-final.json`. The final embed report binds the tested SDK metadata, exact tarball, full frontend build and critical product source hashes. The HTTPS fixture preserves backend hostname/CA verification; Chromium trusts only its generated leaf SPKI, without a global HTTPS-verification bypass. Test accounts, provider, homeserver, databases and processes were isolated; original Docker services, retained pilot data and independent keys were preserved. The retained development API was not restarted for this increment.

The generated artifact is `freecollab-components-0.10.0.tgz`, SHA-256 `de52ea13482c4a55b508fd0034b6274b3b03e460762249c24b0bebcf4121a871`. Its compatibility contract is pre-1.0 and targets matching 0.10.x backend/trusted-client deployments. Live customer Microsoft/Google/other IdP acceptance, external Office/media/provider behavior, npm scope/trusted-publisher ownership, public deployment, a complete browser/load matrix and independent security review remain separate. Browser-origin isolation does not attest a fully malicious host backend; the precise authorization boundary is documented in the identity guide.

```sh
(cd backend && .venv/bin/python -m pytest tests/test_embed_identity.py tests/test_workspace_sso_direct_api.py tests/test_workspace_sso_crypto.py tests/test_sessions.py tests/test_secure_messaging.py tests/test_matrix_proxy_boundaries.py tests/test_matrix_device_revocation.py tests/test_device_revocation_races.py tests/test_release_deployment_kit.py tests/test_security_audit.py -q)
(cd frontend && npm run build && node --test tests/*.test.mjs)
(cd packages/components && npm run release:check)
backend/.venv/bin/python packages/components/tests/browser-proof.py
backend/.venv/bin/python frontend/tests/embed-admin-browser.py
backend/.venv/bin/python packages/components/tests/embed-browser-proof.py --skip-build --matrix
```

## Open-source website and self-hosted launch — 2026-10-21

[The launch guide](OPEN_SOURCE_LAUNCH.md) records the website, account-free demo, first-workspace guide, saved connection overview and optional local feedback. Microsoft 365 and Google remain customer-owned integrations. The public source destination is `freecollab-eu/freecollab`, with a clean history attributed only to Kris Ledel; publishing source does not deploy the website or configure a provider account.

| Area | Executed evidence |
| --- | --- |
| Backend | **217 targeted regression cases passed**, including workspaces, operations, saved connection setup, release integration, **38 voluntary feedback cases**, account exports/erasure and **five new actual account-closure/export cases**. Separately, **14 deployment-readiness cases passed**, including current operator/session revocation, SELECT-only reads, missing/stale/future worker heartbeats and honest unknown off-host evidence. These are targeted runs rather than a new complete backend-suite run. |
| Client | Final TypeScript/Vite build and **162 Node cases passed**. Swedish catalog coverage and interpolation checks passed. No production dependency versions changed. |
| First workspace | **8 actual-App browser groups passed**: real name/logo save, current permission/dirty guards, native channel creation without automatic content, one-time invitation copy/acknowledgement/acceptance/reuse denial/revocation, saved appearance/language, both Google/Microsoft guide paths, Swedish320px, explicit first message with exact delivered ID and encrypted database storage, and ordinary member denial. |
| Optional feedback | **9 real API/App browser groups passed**: explicit save/delete/recreate, encrypted private notes, tombstone revisions, stale-write409 preserving drafts, busy/close guards, fresh role change, unknown summary errors and Swedish320px. Data is local and voluntary; it is not an external usability-study result. |
| Connection and operator overview | **7 actual-App browser groups passed**: exact channel scope, delayed read race, health-to-repair recipe/channel binding, retained draft guards, the actual14-check operator response, Swedish320px and logout. Three helper cases also passed. |
| Public demo | **8 isolated component browser groups passed** for literal local messages, per-channel memory, task actions, exact edited Markdown download, both provider preparation guides, local appearance, reset/focus, reload erasure and Swedish320px. No API/provider calls or demo storage writes occurred. |
| Built public site | **5 production-dist browser groups passed** through the actual App entry: distinct demo/app/source paths, both provider descriptions, saved guest Swedish choice, literal user sample text, FAQ and desktop/390px/320px without horizontal overflow, real `#demo` entry/reload/back navigation and documentation translated at render time. No account API, external provider requests or JavaScript errors occurred. |
| Railway and operations kit | **36 targeted cases passed** for deployment/preflight/scheduling, including verified private TLS, redirect/token/slow-body limits, actual API plus both existing worker CLIs with durable heartbeats, child failure/signal cleanup, bounded storage ownership, backup-stop deferral and the existing authenticated seal/open CLI. **7 native Nginx checks passed** using the copied original binary/libs without starting a container: parsed proxy configuration, trusted/untrusted forwarding, host421 fence, readiness exception and Office capability/error-log privacy. Actual Prometheus parsing passed for **12 alert rules** and the rendered scrape configuration; systemd/JSON/Python/shell checks passed. These prove local kit behavior, not a Railway deployment, off-host backup account or new Docker installation/restore. |
| Retained development preview | API and integration worker were restarted with the new modules. Existing seven core row counts and independent key digests were preserved, the additive feedback table exists, all three new capabilities are advertised and SQLite quick_check is **ok**. No original Docker service was replaced. |

Private current reports under `/workspace/.freecollab/proof-results/` are `workspace-launch-browser.json`, `workspace-feedback-browser.json`, `open-release-health-browser.json`, `demo-showroom-browser.json`, `open-release-public-browser.json`, `railway-native-nginx.json`, `open-release-preview-upgrade.json` and the consolidated `open-release-summary.json`. Screenshots use only synthetic content. Existing historical source/restore evidence below remains a separate baseline.

Browser scripts and the production frontend build are wired into CI. YAML and referenced paths were checked locally; local results do not establish a completed GitHub Actions run. The website has not been deployed to `freecollab.eu` from this environment: the official Railway CLI returned **Unauthorized** for the read-only status check. Customer app registration/consent, live Microsoft/Google tenant acceptance and public DNS/TLS/email/off-host recovery remain deployment-specific work. The self-hosting package and guides prepare those steps without collecting customer credentials centrally.

## Guided customer connections — 2026-10-21

[Connection setup](CONNECTION_SETUP.md) adds 15 preparation recipes and guided entry into the existing customer-owned connection forms. The center provides exact callback addresses, permissions, required fields, identity-provider presets, a credential-free preparation download and current saved-state checks. Opening a recipe never registers a provider app, grants consent, tests a provider or enables a connection.

| Area | Executed evidence |
| --- | --- |
| Backend | **37 setup and independent review cases passed**, including current session/tenant/channel permissions, revocation, disabled features, stale grant revisions, read-only queries and metadata privacy. **214 related regression cases passed** for customer SSO, sync, cloud files, meetings, AI, webhooks, SCIM and release integration. These are targeted runs, not a new full-suite run. |
| Client artifacts | Final TypeScript/Vite build and **128 Node cases passed**. No dependency versions changed. |
| Actual setup journey | **17 private real-App Chromium checks passed**: exact callback/permissions/export, actual encrypted Google/Box/Teams/SSO drafts, busy and unsaved-change navigation guards, immediate SCIM confirmation and one-time-token acknowledgement, webhook and AI forms, endpoint secret clearing, revoked-grant refresh, disabled configuration with preparation still available, Swedish desktop/390px/320px and no horizontal overflow or JavaScript errors. Browser and server tripwires observed **zero public-provider requests**. The report binds the tested snapshot to the current frontend/backend source hashes. |
| Existing provider flows | **10 cloud-file**, **9 external-meeting** and **26 customer-SSO** actual browser checks passed with private fixtures. The SSO fixture uses public-shaped HTTPS issuers with normal production validation; actual TLS, authorization code/PKCE/JWKS, password/MFA, secret retention/rotation, tenant isolation and 320px remain covered. |
| Retained preview | The native API exposes the setup capability. Existing seven core row counts, independent key digests and the running integration worker were retained. The setup proof creates and removes its own API/Vite/database/accounts/keys; it does not use retained runtime identities. |

Current evidence is retained privately under `/workspace/.freecollab/proof-results/`: `connection-setup-browser.json`, `connection-setup-cloud-files-regression.json`, `connection-setup-external-meetings-regression.json`, `connection-setup-customer-sso-regression.json`, `connection-setup-preview-upgrade.json` and `connection-setup-summary.json`. The six `connection-setup-*.png` screenshots show the actual application with synthetic accounts and content. The browser script is wired into CI; workflow YAML and referenced paths were checked locally, but GitHub CI was not executed here.

Customer app registration, tenant administrator consent, provider licences and actual credential entry remain customer setup steps. Readiness is local saved-state evidence rather than a new live-provider test. No live customer provider account, public deployment or new Docker installation/cold-restore run is claimed for this increment. The production/restore evidence below belongs to the preceding frozen 0.9 source. Its consolidated summary retains available historical report snapshots under `product-090-evidence/` and explicitly records the older meeting report whose bytes were superseded by a later regression.

```bash
(cd backend && .venv/bin/python -m pytest tests/test_connection_setup.py tests/test_connection_setup_review.py -q)
(cd frontend && npm run build && node --test tests/*.test.mjs)
backend/.venv/bin/python frontend/tests/connection-setup-browser.py
```

## 0.9 collaboration components and language — 2026-10-21

The eight prioritized Weavy gaps now have implementations. [PRODUCT_090.md](PRODUCT_090.md) records entry points and supported boundaries. The actual app has Swedish/English/browser language selection, personal appearance settings, a compact mobile channel toolbar and a ten-tool selector in the mobile Work dialog. Customer-owned SSO and current membership checks remain in force.

| Area | Executed evidence |
| --- | --- |
| Backend regression | **1,295 passed** in the full suite. After the final integration changes, **283 focused cases passed**, covering the new feature APIs, access races, encrypted-channel boundaries, notifications, content export/retention/account closure and remote transport. These runs overlap and are not a combined unique-test count. |
| Frontend artifacts | Final TypeScript/Vite and widget builds passed; **99 Node cases passed**. Production npm audit reported **zero advisories**. Python runtime dependency versions are unchanged from the preceding hash-locked audit. |
| Language and mobile UX | **16 actual Chromium checks passed**: account-isolated saved language, browser fallback, stale GET/PUT races, independent account queues, preserved draft selection/content, clean production-entry screenshots, all four secondary channel actions with keyboard/focus restoration and all ten Work tools at both 320px and 390px. Unsaved changes still require discard before leaving. Zero page errors or horizontal overflow in these scenarios. |
| Files and Office | **31 feature backend cases**, client checks and **six browser proof groups** passed, including bounded inert previews, real audio/video, immutable save-back, conditional conflicts, secret-redacted DAV logging and actual encrypted Matrix file comments/provider links. The updated Office browser additionally passed **seven checks**. Upload/save authorization and deletion/restore races are included in the final integration run. A vendor desktop Office binary was not available; HTTP WebDAV and browser handoff were exercised. |
| Activity feeds | **17 feature backend cases** and **five actual native/Matrix browser proof groups** passed: posts, comments, revisions, polls, reactions, follow/read state, archiving, notifications and invalid encrypted routing contexts. |
| Cloud files | **59 feature backend/protocol cases**, including **24 actual private HTTPS cases**, plus **nine client cases and ten actual browser checks** passed. Provider grant revisions, same-ID account replacement, immutable imports, tombstones, rotating refresh grants and bounded Box CDN redirects were checked. Google/Graph stable-read regressions also passed. No public provider account was contacted. |
| Copilot | **18 feature backend cases** and **six actual browser checks** passed with a private verified HTTPS/SSE provider, explicit sources/citations, bounded private history, opt-in, native task destination selection and human-approved task creation. Changing endpoints cannot forward a retained API key. Secure context relay requires a separate explicit client disclosure. |
| External meetings | **42 feature backend/protocol cases** and **nine actual browser checks** passed for Google Meet, Teams and Zoom grants, conditional create/cancel, revision fences, uncertain-response recovery and secure calendar references. Zoom deletion uses fingerprint preflight; concurrent provider edits cannot be made atomic. No live tenant/licence was tested. |
| Modular SDK | **14 unit/type checks** and **15 actual packed-package Chromium checks** passed, including standalone Web Components, React StrictMode, customer-scoped async token factories, native uploads/feed/chat/notifications, account switching/logout, Swedish/320px and fresh native-to-secure transition denial before submission. The typed 0.9.0 tarball is retained privately; npm publication was not performed. Encrypted channels fail closed and do not yet have SDK adapter parity. |
| Production and encrypted cold restore | **55 installation checks and 12 recovery checks passed** in a fresh PostgreSQL/Nginx installation. The source and restored installations matched all **42 frontend/widget/config hashes**, all four exact core image IDs, the preceding **14** and new **19 model-row hashes**, authenticated export ZIP bytes, the encrypted export artifact and uploaded-file bytes. Actual built-app Chromium, concurrent last-admin protection, worker CLI batches and security diagnostics passed. Authenticated SecretStream seal/open, tampering, wrong-key and overwrite refusal passed. Both outbound workers stayed stopped after restore. Own source/restore containers, volumes, private configuration and temporary backup keys were removed; the original four service IDs/images/mounts were unchanged. |
| Retained preview | Native API and durable integration worker run **0.9.0**. Existing account/channel/message/workspace/Matrix counts and independent key digests were preserved. Finalizing the empty in-progress cloud-import receipt table changed none of the other **133 tables**; SQLite quick_check returned **ok**. Existing private Synapse and older Docker pilot resources were preserved. |

Actual browser reports are retained privately under `/workspace/.freecollab/proof-results/`: `language-browser.json`, `file-experience-browser.json`, `office-files-browser.json`, `activity-feed-browser.json`, `cloud-files-browser.json`, `copilot-browser.json`, `external-meetings-browser.json` and `component-sdk-browser.json`. The clean `product-swedish-*` and `product-workhub-feed-*` screenshots show the actual production application entry point with synthetic Swedish content. Browser harness probes are excluded from those images.

Production evidence is `/workspace/.freecollab/weavy-install-proof-618c9c58.json`; the consolidated record is `proof-results/product-090-summary.json`. Resource limits required four core containers and real mail/integration worker CLI batches, with the source containers removed before cold restore while retaining their volumes until cleanup. The backend was built from the repository Dockerfile and hash-locked dependencies, then flattened after exact comparison of **10,337 filesystem entries** and runtime configuration. The final frontend used the current native TypeScript/Vite/widget build copied into the same clean Nginx runtime stage; its full Node Docker builder stage was not rerun. Actual Nginx bytes and installed configuration were verified at both source and restore. The final run retained at least **2,853,584,896 free bytes**. An earlier attempt reached **902,352,896 bytes** and then stopped before sealing because the host Python lacked PyNaCl. Its monitor is retained separately; the complete successful run used the locked backend Python after removing three exact unused, unshared builder-cache entries. No images or existing installation data were removed by that cache cleanup. The temporary dependency relocation was reversed after proof cleanup.

Provider features require the operator's feature flags and customer-owned OAuth apps/grants or an AI endpoint/key. External-provider screens/content and arbitrary server/provider errors are not automatically translated. No live customer provider connection, public TLS deployment, npm publication or independent security certification is claimed. New browser scripts, SDK checks and feature tests are wired into CI; YAML and referenced paths were checked locally, but GitHub CI was not executed here.

## 0.8 product tools — 2026-10-21

This increment implements the six requested tools in a shared, appearance-aware channel Work dialog: projects/tasks, browser meetings, PWA/offline/push, concurrent shared documents, workflows and data controls. Customer-owned SSO, channel membership and the native/Matrix encryption boundary remain in force. [The product plan](PRODUCT_PLAN.md) and each tool's linked documentation describe supported limits. [The Weavy comparison](WEAVY_COMPARISON.md) separates current official competitor features from actual gaps in this implementation; it does not claim a hands-on Weavy evaluation.

| Area | Evidence |
| --- | --- |
| Backend | Final full run: **1,135 passed**; one legacy test expected the exact header `no-store` instead of the stronger `private, no-store`. That assertion now checks the required `no-store` directive; the **23-case membership suite** and **failed-only rerun passed**. All **1,136 final cases** were therefore exercised successfully across these runs. **134 focused final checks** also passed after source fixes. |
| Projects/tasks | **19 backend + 8 client cases** and **10 actual Chromium checks** passed: two-browser compare-and-swap conflicts, assignment/deadline/checklists, exact linked file bytes, current decision details, revision history, archive/reopen, outsider denial and 320px. Native payloads were encrypted in SQLite. |
| Meetings | **26 backend cases + 12 actual Chromium checks** passed with native audio RTP and decoded video, real screen-track replacement/restoration, three-browser guest admission, fresh participant identities after native/guest rejoin, removed/stale capability denial, transient HTTP failure cleanup and 320px. The environment's managed Chromium UDP policy was preserved; an explicit private loopback TURN/TCP fixture supplied the tested transport. |
| Mobile/offline | **34 backend cases + 11 actual production-app Chromium checks** passed. Native service worker control, encrypted IndexedDB/nonextractable keys, public-only CacheStorage, offline hard reload and draft editing, no cached private catalog/history, safe reconnect, lost-response deduplication, revoked membership, two-account isolation, logout purge and the restore-versus-new-typing race were exercised. A private HTTPS/CONNECT receiver independently decrypted actual Web Push aes128gcm and verified VAPID; no live provider was contacted. |
| Shared documents and actual Matrix | **18 backend + 5 client boundary cases** and a combined **11-check native/secure browser proof** passed. Two users' offline concurrent Yjs edits merged and survived fresh reload. Comments, exact Markdown export and restoration preserve immutable history. Two trusted Matrix JS/Rust devices exchanged large encrypted chunked documents, comments and snapshots; reconstructed history matched. The same live Matrix proof verified peer task edits/history and encrypted workflow approval notes; native API/DB held opaque references and the actual wire held m.room.encrypted events. |
| Workflows | **26 backend cases + 6 actual browser/worker checks** passed: visual builder, actual file upload trigger with external webhooks off, durable worker restart, reminder/escalation, approval, real notifications/task creation and immutable configuration/vote/activity history. Secure approval notes additionally passed the live Matrix proof above. |
| Data controls | **20 implementation + 6 independent targeted review cases** and **6 actual Chromium checks** passed using private destructive fixtures only. Fresh native owner/password/MFA, hold/policy/preview fences, encrypted exact export bytes, symlink-safe file access, metadata and physical file erasure/retries, receipt tombstones, unknown dependent-FK refusal, stale-worker ownership, account/owner handover and disabled authorship stubs were checked. Export builds use anonymous unlinked temporary ZIPs; durable artifacts use authenticated SecretStream encryption. |
| Artifacts/dependencies | TypeScript/Vite and widget builds passed with **80 Node cases**. Exact Yjs 13.6.27 and pywebpush 2.5.0 plus hash-locked transitives are installed. Production npm audit reported zero advisories; Python audit reported zero known vulnerabilities across 63 locked runtime packages. |
| Production and encrypted restore | Final unchanged production images passed **46 checks + 17 restore checks**. A private PostgreSQL installation exercised exact project/task/document/workflow/export state, actual built-Nginx UI at 320px and zero page errors, SCIM/managed identities, 14 security diagnostics and all six integration queues. Concurrent native account closures returned one 200 and one 409, preserving the last admin. Authenticated SecretStream cold backup restored 14 exact model-row hashes, the sealed export file and identical authenticated ZIP bytes. Wrong keys/tampering/overwrites were rejected. Both separate worker services stayed stopped at restore. **Resource limit:** VFS full-container clones exhausted the initial six-service empty startup; it was removed. The final proof used four healthy core containers and real mail/integration worker CLI batches via backend exec, rather than simultaneous standalone worker containers. The smaller backend image performed the same offline read-only Matrix YAML backup check; 58 backup/seal regressions passed. Own projects/volumes/env/keys were cleaned; original four service IDs/images/mounts were unchanged. |
| Retained preview | Native API and durable integration worker run **0.8.0**. Existing account/channel/message/workspace/Matrix counts and account/content key digests were unchanged by upgrade. SQLite quick_check returned ok after storage recovery; original private Synapse and older Docker pilot resources were retained. |

The actual browser reports are private synthetic evidence under `/workspace/.freecollab/proof-results/`: `project-tasks-browser.json`, `meetings-browser.json`, `mobile-offline-browser.json`, `shared-documents-browser.json`, `workflows-browser.json` and `data-controls-browser.json`. The production evidence is `/workspace/.freecollab/product-install-proof-37df9e8a.json`; a consolidated result is `proof-results/product-080-summary.json`. Desktop/mobile screenshots were inspected. Owned temporary API/Vite/worker/relay/browser/database/key fixtures were cleaned. Public-only PWA asset caching is complemented by private no-store HTTP headers for API success, error and streaming responses.

External-network TURN acceptance, public TLS, browser push enrollment and real customer provider credentials remain deployment-specific. Meetings use WebRTC DTLS-SRTP with server signaling, separately from Matrix trusted-device verification. The PWA is a browser application, not a native iOS/Android package. Retention defaults to disabled and on hold; native export/erasure cannot decrypt or erase Matrix history on remote devices, provider copies or backups. Closing a personal account erases its identity and credentials while retaining an opaque disabled author for shared history. No real customer data was purged and no public deployment or third-party security audit was performed.

New browser scripts are wired into CI and the YAML/script paths were checked locally; GitHub CI itself was not executed here.

## 0.7.1 security and operations — 2026-10-21

The operator panel now includes private security configuration, MFA coverage, storage and queue-lease diagnostics, plus a bounded activity trail for selected authenticated security actions. Customer SSO and sync race findings were reproduced and corrected. Cold restoration holds both outbound workers stopped by default; an optional authenticated encrypted backup bundle protects copies intended for separate storage. [SECURITY_OPERATIONS.md](SECURITY_OPERATIONS.md) and [OPERATIONS.md](OPERATIONS.md) describe the controls and their boundaries.

| Check | Executed result |
| --- | --- |
| Complete backend regression | **959 passed**, no failures or skipped tests. The final backup-descriptor cleanup regressions were added after suite collection and additionally passed in the focused final 58-case backup run below. |
| SSO secret retargeting | **20 tenant cases passed**, including eight new cases. A native administrator could previously change the issuer/client and retain a secret they could not read. Canonical issuer/client changes now clear omitted/blank secrets; explicit replacement, same-identity retention and public clients remain supported. The old failure was reproduced before correction. |
| Sync authorization races | **38 sync cases passed**, including eight new adversarial cases. OAuth callback and provider test/enable now check the current native session/security version, connection revision and acting administrator's current workspace/channel permissions after lock waits and network operations. Stale callbacks cannot reinstall a previous account grant into a retargeted connection. |
| Stable remote file reads | **36 Google/Graph cases passed**. Actual verified HTTPS/CONNECT fixtures exercise conditional Google downloads and authenticated post-download metadata checks. A changed revision, moved file, MIME change or HTTP 412 cannot import bytes labelled with an earlier snapshot or outside the selected folder. Graph download authorization is not forwarded to preauthorized media hosts. These checks do not create a remote atomic transaction. |
| Private security diagnostics | **35 new cases**, plus 13 existing operations cases, passed together. Native installation admins alone can read the 11 fixed checks; ordinary/workspace/scoped admins and metrics-only credentials are excluded. Tests cover canonical HTTPS/CORS, explicit independent keys, operator MFA aggregates, disk thresholds, due/failed/expired or invalid leases across four queues and disabled-feature handling. Reads neither contact providers nor decrypt secrets/read key files. |
| Security activity trail | **12 cases passed**: real login, denied privilege changes, distinct SCIM actor, no request/credential/email payloads, anonymous/content-route exclusion, native-admin-only reads, malformed cursors, tied-timestamp pagination, age retention, record limits under concurrent writers and generic failure handling. PostgreSQL production proof additionally executed its advisory-lock path with five concurrent real logins. |
| Client artifacts | Final 0.7.1 TypeScript/Vite build and **67 Node tests passed**. Dependency versions and widget sources are unchanged. |
| Actual operator browser | **9/9 checks passed** with a private real API, Vite, Chromium, durable audit middleware and file-mail worker. Exercised native operator/member/scoped/metrics boundaries, MFA/storage/lease status, actual success/denial events and redaction, filter/cursor pagination, 90-day/100,000-record configuration, wrong-password session retention and real private test delivery, independent endpoint failure, keyboard focus and 320px. Retained fixture metadata is synthetic. Zero page errors or credential leaks; desktop/mobile screenshots were inspected. |
| Existing account and SSO flows | The self-contained native account browser passed verification, workspace create/join, sessions, MFA enrollment/challenge/recovery acknowledgements, password recovery and mobile focus again. The actual two-customer HTTPS SSO browser passed **26/26 checks** again with personal native linking, MFA, revision/disable, recovery unlock and native fallback. |
| Actual managed client encryption | Real Matrix JS/Rust devices passed the six-stage managed proof again: a separate organization identity despite personal email collision, verified-peer decryption and opaque wire events, denial of another workspace, direct personal-ciphertext decryption failure, fresh-device recovery from its own key, and recent-SSO owned-device/session revocation. |
| Backup crypto and quarantine | **58 final tests passed**, including an independent internal rerun. Tests cover independent private keys, exact authenticated artifact roundtrip, tampering/wrong key/truncation/missing final tag/trailing data, unsafe or extra artifacts, overwrite refusal, FIFO rejection, directory substitution during publication/decryption, cleanup of original staged plaintext and descriptor closure after faults. CLI orchestration verifies independent `--resume-mail`/`--resume-integrations` flags and original running-state handling. |
| Production and encrypted cold restore | Final hash-locked backend and Nginx frontend passed 30 production checks with six healthy services and PostgreSQL. Five concurrent real logins persisted redacted audit rows through PostgreSQL advisory locks; private diagnostics, read-only metrics exclusion, SCIM/managed identity and encrypted DAV state passed. An actual cold backup was sealed with a separate private key, opened with exact artifact-hash equality, and restored only from that authenticated copy. Wrong key, corrupted final authentication and overwrites were refused. The restored exact-image installation retained accounts/groups/credentials and audit rows, running exactly four core services with both outbound workers held stopped. All disposable projects, volumes, configuration, backup copies and sealing keys were cleaned; original service IDs/images/mounts were retained. |
| Retained preview | Native API/integration worker run 0.7.1, with existing user/channel/message/workspace/Matrix counts and account/content key digests unchanged. The additive security-event table was initialized. Existing private Synapse, older Docker pilot and native preview services were retained. |

The activity trail stores fixed action codes, opaque actor/target/workspace IDs, timestamps and HTTP outcomes. It excludes content, credential bodies, query strings, network addresses and anonymous failed attempts. Existing authentication limit counters handle anonymous failures. Persistence happens separately after the action, reports a generic server-log failure if unavailable, and is not an immutable, external or transaction-complete audit ledger. Defaults are 90 days and a 100,000-record target; bounded pruning converges incrementally after lowering a previously larger cap, while age filtering applies immediately. Backups can retain earlier records.

The diagnostic checks establish local configuration and retained queue state, not public certificate validity, provider delivery, off-site backup scheduling or independent security certification. SecretStream bundles require an independent retained sealing key; the tool preserves the original plaintext backup and does not upload anything or export Docker images. Restoring an authenticated opened copy still requires retained exact images and a new private target. Both restored outbound workers remain stopped until explicitly requested.

All security/provider/browser proofs used private fixtures. No real customer provider credentials, external SMTP message, public deployment, off-site scheduler or third-party security audit was performed. The production proof used trusted configured HTTPS origins with loopback Nginx HTTP, not newly provisioned public TLS. New browser proof and backup tests are wired into CI; YAML and paths were checked locally, not run on GitHub.

```bash
(cd backend && .venv/bin/python -m pytest tests/ -q --disable-warnings)
(cd backend && .venv/bin/python -m pytest tests/test_pilot_backups.py tests/test_backup_seal.py -q)
(cd frontend && npm run build && node --test tests/*.test.mjs)
backend/.venv/bin/python frontend/tests/security-operations-browser.py
backend/.venv/bin/python frontend/tests/customer-sso-browser.py
```

## 0.7 workforce onboarding, SCIM and two-way sync — 2026-10-20

Workspace administrators can choose default personal-account linking or separate managed workforce admission through provisioning, organization invitations or explicit just-in-time policy. Customers configure their own IdP and directory; cloud file/calendar authorization uses their own separate OAuth app/account consent or DAV app password. [WORKFORCE_IDENTITY.md](WORKFORCE_IDENTITY.md), [SCIM.md](SCIM.md) and [REMOTE_SYNC.md](REMOTE_SYNC.md) describe the supported behavior and limits.

| Check | Executed result |
| --- | --- |
| Complete backend regression | **858 passed**, no failures or skipped tests, including final workforce/SCIM authorization, managed-identity isolation, native account regressions, retargeted-credential clearing and grant-owner checks. The final narrow Google Drive trash change happened after this suite was collected; the final adapter suite below verifies that exact change. |
| Final provider change | **33 Google/Graph cases passed** on the final adapter. A real verified HTTPS/CONNECT fixture proves Drive file removal uses conditional `PATCH {trashed:true}`, never permanent DELETE; stale versions and HTTP 412 races do not trash the file. The earlier combined provider/transport/DAV run passed 113 cases; transport and DAV sources were unchanged by this narrow fix. |
| Client artifacts | Final Office/ODF-aware client passed TypeScript, production Vite build and **67 Node tests**. The widget bundle also built; its sources and dependency versions were unchanged. Existing optional large-chunk warnings remain. |
| Managed onboarding and directory UI | **18/18 actual browser checks passed** using a real API/Vite/Chromium and synthetic HTTPS IdP. Native MFA, one-use organization invitation, first SSO without native signup/password, an unverified provider email colliding with a personal owner, separate identity, SCIM Users/Groups, channel grant/revoke/readd, token rotation, active-false issued-session rejection, tenant isolation, explicit JIT consent, native fallback and 320px passed. Zero page errors or secret leaks. |
| Managed cryptographic isolation | Actual Matrix JS/Rust devices proved separate managed and personal identities despite the same displayed email. Verified peers decrypted both ways and wire events remained opaque. Another workspace's room returned 403; its ciphertext failed decryption when handed directly to managed Rust crypto. A fresh managed device restored its own encrypted history using its separate recovery key and still could not decrypt personal ciphertext. Recent organization SSO revoked an owned lost device and its linked session without a native password. |
| Existing personal SSO and crypto | The actual two-customer browser passed **26/26 checks** again, preserving explicit personal linking, native MFA, provider revision/disable and fallback. The real personal scoped Rust proof passed again: IdP-only crypto denial, native unlock, verified-peer decryption, fresh-device recovery and another workspace's room denial. |
| Native account regression | An isolated real API, private mail worker, Vite and Chromium passed verification, workspace create/join, session controls, MFA enrollment/challenge/recovery acknowledgements, password recovery, keyboard focus and 320px. It removed its own temporary services, database, keys and mail. |
| Actual two-way sync UI and worker | **17/17 browser checks passed** with a trusted synthetic HTTPS WebDAV/CalDAV server, real API and durable worker. Bidirectional file bytes/calendar events, stable UID, immutable versions, both conflict choices, failure/retry/pause, current member/cross-tenant/private-address/secure-channel fences, sharing notice, credential-retarget clearing before any new destination contact, focus and 320px passed. Zero page errors or credential leaks. |
| Office files | **7/7 actual browser checks passed**: valid DOCX upload through the file picker, a new version of that document, accurate labels, retained immutable versions, exact current/history download bytes, Office MIME plus attachment/nosniff headers, download-only behavior and 320px. |
| Independent targeted review | An additional **12 SCIM review cases passed**. Concrete review findings—DAV calendar transport metadata, malformed SCIM filter handling, stale target/account credentials and Drive permanent-delete semantics—were corrected and covered by regressions. This was implementation review, not an independent external security audit. |
| Production installation and cold restore | Final hash-locked backend and production Nginx frontend passed with six healthy services, actual production settings and PostgreSQL. Native owner/policy setup, SCIM bearer Users/Groups, synthetic managed namespace despite personal email collision, encrypted/redacted DAV draft, additive schema/idempotence and all three worker queues passed. Actual cold backup/restore preserved the SCIM credential hash, managed identity/group, encrypted DAV configuration, native session, independent keys and exact final image IDs. The restored outbound integration worker remained stopped. Only disposable proof projects, volumes and private configuration were cleaned; existing services were retained. |
| Retained preview upgrade | Native API and integration worker now run 0.7.0 with sync dispatch enabled and no configured external provider connection. Existing counts were retained: 187 users, 99 channels, 805 messages, 46 Matrix accounts, 52 Matrix sessions, 57 workspaces and 160 workspace memberships. Independent account/content key file digests were unchanged; new additive workforce/directory/sync tables and a live worker heartbeat were verified. Existing private Synapse and older Docker pilot services were retained. |

Background synchronization supports selected native channels with server-readable file/calendar content, direct-child folders and the supported ordinary event formats. Secure Matrix channels, including encryption preparation, reject server background sync. Their content remains on trusted devices. Native files retain their existing storage model; synchronization does not turn their bytes into end-to-end encrypted files. Provider-controlled deletion and local retained history are separate; Google Drive uses trash, while DAV/calendar removal has no promised recycle bin.

Default linked personal SSO still needs an existing verified native account and explicit password/MFA linking; its account-wide Matrix material requires a separate grant of at most 30 minutes. Managed identities are distinct per workspace, require valid scoped SSO, and never merge through email. Their own recovery keys and device verification remain necessary. Connected SCIM deactivation revokes managed access; an IdP-only disable without provisioning synchronization takes effect at the next sign-in.

All provider proofs used synthetic fixtures and private temporary data. No real Google/Microsoft/Nextcloud customer account, actual customer tenant consent, public DNS/TLS deployment or independent security audit was performed. The installation test used production settings with a trusted configured HTTPS origin and loopback Nginx HTTP. CI defines the new onboarding, sync, Office and managed Rust harnesses; workflow YAML and paths were checked locally, not executed on GitHub.

```bash
(cd backend && .venv/bin/python -m pytest tests/ -q --disable-warnings)
(cd frontend && npm run build && node --test tests/*.test.mjs && npm run build:widget)
backend/.venv/bin/python frontend/tests/enterprise-onboarding-browser.py
backend/.venv/bin/python frontend/tests/remote-sync-browser.py
backend/.venv/bin/python frontend/tests/office-files-browser.py
# Existing private Matrix settings and Vite are required; do not replace its identity:
backend/.venv/bin/python scripts/managed-sso-secure-browser-test.py
backend/.venv/bin/python scripts/workspace-sso-secure-browser-test.py
```

The self-contained browser proofs accept `BACKEND_PYTHON` and `CHROMIUM_PATH`. They create and remove their own private API, Vite, databases, keys and provider fixtures. Rust proofs use the configured private homeserver, create their own API/database, and accept optional `--chromium`. Credentials and client recovery keys are never printed.

## 0.6.1 customer-owned SSO correction — 2026-10-20

Each customer workspace now configures its own OIDC provider in **Integrations → Sign-in**. Customer administrators save a disabled draft, test and enable it, then share their own opaque workspace sign-in link. The customer path works with the legacy installation-wide `OIDC_ENABLED=false`. [OIDC.md](OIDC.md) documents setup and the exact API.

| Check | Executed result |
| --- | --- |
| Final backend suite | **670 passed**, no failures or skipped tests, on the final source including scoped recovery metadata handling. Covers customer providers, scope/revocation across REST/socket/Matrix, transport, native account behavior and previous product features. |
| Client and artifacts | **67 Node tests passed**. Final frozen frontend passed TypeScript, `npm run build` and `npm run build:widget`; dependency versions are unchanged. |
| Customer OIDC and transport | 12 customer protocol/configuration/revision/race cases plus all 27 legacy OIDC cases passed. 49 transport tests passed, including actual HTTP/HTTPS CONNECT tunnels, public-address/DNS pinning, original-host TLS verification, private/mixed DNS rejection and bounded requests. Saved client secrets are workspace-bound encrypted values and never returned by configuration reads. |
| Actual two-customer browser | **26 checks passed**, using two independent synthetic HTTPS IdPs, separate clients/secrets, real API/Vite and Chromium. Exercised owner-only setup/test/enable, wrong-password rejection with session retention, native MFA, explicit verified-member linking, actual scoped sign-in, no account provisioning, wrong-issuer refusal, secret retention/rotation, customer-specific disable and native MFA fallback at 320px. Zero page errors. Safe desktop/mobile screenshots were inspected; no page overflow. |
| Scoped encryption and fresh recovery | A real private Synapse and actual Rust crypto clients proved IdP-only Matrix credential denial, native password unlock, verified-peer decryption in both directions and opaque wire events. Another workspace's known room remained HTTP 403 after unlock. A fresh scoped browser/device restored encrypted history using its separate recovery key and still could not access the other workspace. Crypto account-data acknowledgements are allowlisted after native unlock; global conversation/account preferences remain hidden. |
| Encryption unlock UI | Real scoped token, wrong-password rejection and successful native password/MFA grant passed in the actual UI. Only Matrix availability in the status response was a presentation fixture; it created no Matrix session. The separate real Rust proof above exercised actual encryption and recovery. |
| Existing behavior | The full native two-browser crypto proof passed: encrypted titles, threads/edits/reactions, encrypted files, backup, fresh-device recovery, member removal and direct future-ciphertext decryption refusal. The native account browser passed verification, invitations, sessions, MFA enrollment/replacement/recovery-code acknowledgement, password reset, focus and 320px. The isolated legacy operator OIDC browser also passed without changes. |
| Production install and restore | Fresh hash-locked backend and Nginx frontend passed with **six healthy services**, actual production settings and PostgreSQL. Native login/workspace creation, owner-only draft config, API secret redaction, ciphertext/decryption parity, additive schema, startup idempotence and worker heartbeats passed. Actual cold backup/restore retained the encrypted provider, native session, independent keys and exact images; the restored outbound worker remained stopped. Disposable proof resources were cleaned; existing services were retained. |
| Retained preview upgrade | The existing native preview was updated to 0.6.1. Startup retained account, channel, message, Matrix account and Matrix session counts and added the new customer provider/session/grant tables. The final scoped recovery fix was loaded afterward. Existing private Matrix and Docker pilot services were retained. |

Customer SSO requires an existing, email-verified native account, current membership and explicit native password/MFA identity linking. It does not create/verify global accounts from IdP email claims, assign roles or perform SCIM/group synchronization. Provider edits/disabling and membership removal invalidate scoped access; disabling a person only at the IdP does not retroactively revoke an issued FreeCollab session.

The personal Matrix identity and backup remain account-wide. Customer SSO alone cannot retrieve them. A separate native password/MFA grant lasts at most 30 minutes on that exact scoped token; workspace data stays scoped after unlock. Device trust and the separate recovery key still apply. This is not cryptographic separation of independent per-workspace personal identities.

The production smoke used loopback HTTP Nginx with a configured trusted HTTPS public origin; public TLS was not provisioned. The browser/transport/crypto proofs used synthetic providers, private databases and fixture accounts. No real customer IdP connection, public deployment or independent security audit is claimed. CI includes both the customer OIDC browser and scoped Rust recovery harness; YAML was checked locally, not executed on GitHub.

```bash
(cd backend && .venv/bin/python -m pytest tests/ -q --disable-warnings)
(cd frontend && npm run build && node --test tests/*.test.mjs && npm run build:widget)
backend/.venv/bin/python frontend/tests/customer-sso-browser.py
# With an already configured private Matrix service and Vite client:
backend/.venv/bin/python scripts/workspace-sso-secure-browser-test.py
```

The self-contained customer browser accepts `BACKEND_PYTHON` and `CHROMIUM_PATH`. The scoped Rust proof accepts `BACKEND_PYTHON`, optional `--chromium`, and the existing private `MATRIX_*` connection settings; it creates and removes its own API/database. Never replace an existing homeserver identity for a test.

## 0.6 integrations candidate — 2026-10-20

The product now includes operator-configured OIDC sign-in, signed channel-granted webhooks/n8n, document links, calendar/ICS exchange, optional hash-only OTRUST version proofs and private mail/worker diagnostics. [INTEGRATIONS.md](INTEGRATIONS.md) describes configuration, access and encryption boundaries. Remote Drive/SharePoint/CalDAV synchronization and automatic IdP/SCIM offboarding are not claimed.

| Check | Executed result |
| --- | --- |
| Final complete backend suite | **578 passed**, no failures or skipped tests. Covers previous product behavior plus OIDC/MFA/session races, scoped webhook dispatch, content revisions, calendar validation, version-proof queues, operational roles and integration worker isolation. |
| Final client Node suites | **67 passed**, including the existing client/widget checks, 11 document/calendar cases, 3 proof cases and 5 tests running the exact copied n8n raw-body verifier. |
| Final TypeScript / production artifacts | The final frozen 0.6 source passed `npm run build` and `npm run build:widget`. Crypto/PDF assets remain local; existing large optional chunk warnings do not prevent compilation. |
| OIDC protocol and browser | 27 dedicated backend cases and earlier native account/session regressions passed. A real HTTPS synthetic IdP with trusted fixture CA, PKCE and RSA/JWKS exercised signup/returning login, one-time handoff/no bearer URLs, email collision, password/MFA explicit linking, local MFA on SSO login, failed unlink retention, successful all-session revocation and native fallback at 320px. Zero browser page errors. Reproduced StrictMode handoff/error-route failures were fixed and the complete flow rerun. |
| Webhooks | 29 dedicated backend cases passed, including a real HMAC receiver and real CONNECT-proxy/TLS fixture with original-host verification. Tests cover public-IP pinning/private and mixed-DNS refusal, finite deadlines, exact raw bytes, retry/lease recovery, deduplication, rotation, stale grants and role isolation. The actual dashboard exercised create/edit/pause/resume/rotate/test/retry and one-time secret handling; no public receiver was dispatched to. |
| Documents and calendar | 19 backend and 11 client cases passed, including native-history secure-enable fencing. Real browser create/edit/history, DST event, ICS export/import and UID deduplication passed at 320px. Two actual Rust crypto devices read secure document/calendar fields; wire/API/database checks found only opaque native pointers, and secure ICS export happened locally. |
| OTRUST | 16 backend and 3 client cases passed. Real browser consent, durable local-provider claim/pending lifecycle, exact real `.ots` download, immutable version separation, decision canonical bytes and 320px layout passed. OpenTimestamps parsed proofs and checked digest commitments; independent Bitcoin-chain verification remains false and is not advertised. No hosted claim was made. |
| Mail and monitoring | 17 focused cases passed, including real loopback STARTTLS, implicit TLS, SMTP authentication/byte delivery and rejection of an untrusted CA. The actual operator UI checked fresh password/MFA, private file delivery, heartbeat/metrics, and member-role exclusion. The optional metrics token is read-only and rejected by other routes. No external SMTP mailbox was contacted. |
| Existing browser regressions | All seven pilot stages and the complete account verification/workspace/session/MFA/reset/mobile flow passed on 0.6. The account harness now waits for the fresh resend message rather than racing a superseded signup token. |
| Frozen-source installation | Fresh hash-locked backend and production Nginx frontend ran with PostgreSQL, private Synapse, mail worker and integration worker. Six-service readiness, both worker heartbeats, one-shot polling, private mail delivery and scoped metrics denial passed. Actual Chromium native sign-in, document/calendar persistence, ICS export/import and 320px layout passed with no page errors. Tested source hashes and exact image IDs were retained; outbound providers stayed disabled. |
| Current cold backup/restore | The six-service manifest included the integration-worker image. A fresh serial restore preserved document/calendar records and exact images while keeping the restored outbound integration worker stopped by default. Only disposable proof projects, private configuration and volumes were removed; existing installations were retained. |
| Dependency audit | The hash-locked Python environment was synchronized with PyJWT cryptography, httpcore and OpenTimestamps additions. PyPI-service audit of 71 installed packages found **zero known vulnerabilities**. The npm dependency versions are unchanged from the earlier dated client audit. |

The new self-contained browser harnesses use private temporary databases/provider fixtures and remove their own processes, keys and data. OIDC, dashboard, document/calendar and OTRUST checks are wired into CI; YAML was checked locally, not executed on GitHub. Provider tenant consent, external SMTP/DNS/TLS, live n8n deployment, hosted Bitcoin anchoring and an independent security review were not performed.

```bash
(cd backend && .venv/bin/python -m pytest tests/ -q --disable-warnings)
(cd frontend && npm run build && npm run build:widget && node --test tests/*.test.mjs)
python frontend/tests/integrations-browser.py
python frontend/tests/oidc-browser.py
python frontend/tests/proofs-browser.py
python frontend/tests/document-calendar-browser.py
```

The proof harness needs the normal Vite client; the document/calendar harness additionally requires a disposable Matrix-enabled API and `CONTENT_DATABASE_URL` matching that API's database. The two self-contained integration/OIDC harnesses create their own local services. See each script header before running them. Hosted credentials are not needed for these synthetic protocol checks.

## 0.5 product candidate — 2026-10-20

The eight workstreams now have working implementation: account security, complete authorized history and local search, mentions/watches/notifications, isolated workspaces and guests, versioned files, saved items and decisions, canonical direct messages, and optional client-encrypted channels. Existing server-encrypted channels remain a separate explicit mode. [E2EE_IMPLEMENTATION.md](E2EE_IMPLEMENTATION.md) describes the selected protocol and operational boundaries.

| Check | Executed result |
| --- | --- |
| Full backend suite | **455 passed** with no failures or skipped tests. Includes account token/TOTP concurrency, role/guest isolation, canonical DM races, persistent mail, file/version integrity, message cursors/tombstones, decisions, Matrix gateway boundaries and competing native-write/secure-enable transactions. Final device-revocation additions also received a focused combined gateway/device/race run after the last lock-coverage change, as recorded below. |
| Client Node suites | **48 passed**, including stale-account responses, appearance/commands/widget, acknowledged uploads/cancel/retry and real Matrix attachment integrity/ownership tests. |
| TypeScript and production build | Passed with Node 24 and the locked Vite/Matrix/PDF dependencies. Large optional crypto/PDF chunks are emitted separately; no external font or PDF-worker CDN is used. |
| Account and workspace browser flows | Real private mail verification, password recovery, session revocation, TOTP enrollment/login/backup codes, workspaces/invitations, 320px layout and delayed old-account response guards passed. |
| History/search browser flows | More than the first history window loaded, oldest result found locally, edits/deletion reconciled, older context opened, member-ID mention picker/inbox passed. Expanded file/decision search and type filters also passed. |
| Files browser flows | Multi-upload, drop, progress/ACK, cancel/retry, lost response deduplication, immutable version downloads, safe image and multi-page local PDF previews, blob cleanup and 390px layout passed. |
| Collaboration browser flows | Private bookmarks/shared pins survive reload; decisions/revisions/history, thread watches, channel/global notification preferences and mention notification navigation passed. |
| Existing client regressions | Pilot, chat, appearance and elite writing/reading/dialog workflows passed. The pilot explicitly selects legacy mode when testing native-server behavior on an installation with Matrix enabled. Frozen production assets were used for the final pilot/appearance/elite checks to avoid HMR changing the page during tests. |
| Secure account UI | Real encrypted DM provisioning, one-time recovery-key acknowledgement, memory lock/unlock, second browser, mismatched fingerprint rejection/correct verification, personal archive/restore/block/unblock passed. With MFA enabled, wrong-password device removal retained the session/device; successful own-device removal revoked its native session while retaining the active browser. Device-list refresh and current-device removal/sign-out passed at 320px with no page errors. |
| Built Docker browser assets | Both Nginx variants passed actual Rust device initialization, 700-export WASM streaming compilation and local PDF module-worker/canvas rendering. The test reproduced and fixed the original PDF `.mjs` MIME defect. |
| Cold backup/restore v2 | Isolated PostgreSQL/app history, private mail and Matrix SQLite/config/signing/media restored into a new project with exact image IDs. Bytes, signing identity, ownership/modes, health and unchanged source were checked; unsafe/occupied targets refused. This cold-restore proof used retained app images. |
| Current-source five-service installation | Fresh builds ran PostgreSQL, API, Nginx, private Synapse and the mail worker. Real workspace/message/thread/file access and private mail delivery passed. After the final device-revocation changes, a new frozen-source installation passed the full Chromium encrypted DM/recovery/fingerprint/archive/block workflow, MFA, wrong-password device removal, other-device JWT revocation and current-device sign-out at 320px with no page errors. Only the loopback frontend port was published. Source hashes and exact image IDs identify the tested build. Its disposable containers, network, volumes and private configuration were removed; existing installations were retained. |
| Locked Python environment | Fresh Python 3.11 environment installed from the regenerated development hash lockfile and imported the complete application successfully. |
| Production npm dependencies | `npm audit --omit=dev --json`: zero known production dependency advisories at the check time. |

The full `scripts/secure-browser-test.py` run passed: two isolated browsers verified encrypted titles and opaque wire events, replies/edits/reactions, real encrypted attachment delivery/integrity/retry, cross-signing/secret storage and encrypted backup, memory lock/unlock, a third fresh browser with a new native session/Matrix device restoring edited history, and remote-first member removal with future sends and denied old-recipient access. The final strengthened run also handed the future ciphertext directly to the removed recipient’s Rust decoder and confirmed decryption failure, proving more than an HTTP access denial. Setup waits for the initial encrypted backup to acknowledge completion before returning the one-time recovery key; this fixed a reproduced fresh-device restoration failure. A dependency's audited protocol implementation does not establish an independent audit of FreeCollab. No public deployment, external SMTP delivery, DNS/TLS provisioning, SSO or OTRUST proof was performed.

The focused `scripts/secure-device-browser-test.py` proof also passed with three real browser crypto engines. Both of one account's devices read prior content. After confirmed Synapse device deletion, its native session returned 401. Deliberately blocking device-key queries preserved the sender's stale Rust cache, and even encrypted metadata sending failed closed. Once normal sync refreshed that cache, the retained device read a new rotated-session message. The revoked device failed to decrypt the same future ciphertext handed directly to its Rust decoder; already-held history keys were retained. The final combined secure-channel/gateway/device/race backend run passed **24 tests** after the last implementation change. Regressions include authentication changes while waiting for locks and recipient-set changes during device removal.

Reproduce the current checks with the commands below and the headers of the new browser harnesses:

```bash
(cd backend && .venv/bin/python -m pytest tests/ -q)
(cd frontend && npm ci && npm run build && node --test tests/*.test.mjs)
python frontend/tests/account-browser.py
python frontend/tests/account-race-browser.py
python frontend/tests/history-browser.py
python frontend/tests/files-browser.py
python frontend/tests/collaboration-browser.py
python frontend/tests/secure-account-browser.py
python scripts/secure-browser-test.py
python scripts/secure-device-browser-test.py
```

The native browser tests require disposable accounts/services. Account email-link tests require the private file-mail worker and `ACCOUNT_MAIL_PATH`. Secure tests additionally require `MATRIX_ENABLED=true`, the private Synapse service and verified devices; they do not silently skip encryption. `production-assets-browser.py` runs against an actual built Nginx container and is distinct from Vite checks. CI now defines separate native-product and secure-protocol jobs, including lost-device and encryption-account UI checks; the workflow was checked locally, not executed on GitHub in this task.

The following record predates 0.5 and is retained as historical evidence. Its earlier content/account limitations do not describe the new secure-channel path.

## Historical 0.4 pilot candidate

The current implementation adds full channel-invitation/member controls, the reference client's core team/file workflow, current-token logout and auth throttling, separate versioned content keys and migration, hash-pinned dependency installs, private Compose installation and recovery tools. The reference client now also has a Scandinavian-inspired design, account-owned appearance preferences, a quick switcher, multiline writing and reading-position controls; see [APPEARANCE.md](APPEARANCE.md) and [DESIGN_SYSTEM.md](DESIGN_SYSTEM.md). Advanced posts/comments/mentions and server AI-demo APIs are disabled by default. These implementation descriptions are not a completed external-pilot claim.

The following checks passed on 2026-10-20 for the self-hosted 0.4.0 pilot candidate. Browser checks of the redesigned client use Vite with SQLite. The earlier Nginx/PostgreSQL Compose and recovery results are identified separately below; that stack has not been rerun with the redesigned client. These checks do not establish an externally published service.

| Check | Current release result |
| --- | --- |
| Combined backend suites | 288 passed with no failures or skipped tests, including the legacy root API tests, appearance preferences and the notification-preference fix. |
| Notification preference and flow suites | 51 passed: 29 new notification tests plus 22 existing flow-access tests. Checks cover all six category mappings, in-app suppression, defaults, unknown types, actual comment generation and retained access/self-notification rules. |
| Frontend type check/production build | Passed with Node 24, Vite 7 and Tailwind 4. |
| Widget regression suite/build | 8 passed and bundle build passed. All 4 Chromium browser groups passed against the current Vite/SQLite services: history/XSS, acknowledged send/polling, switching and outsider denial. |
| Frontend Node regression suites | 33 passed: 5 appearance, 8 widget, 8 command-search and 12 API-session tests. Appearance covers normalization, system mode/DOM tokens and tested contrast pairs. Command tests cover matching, scope and keyboard result navigation; session tests cover current/old/anonymous request handling for JSON, uploads and downloads. |
| Personal appearance browser checks | All 7 groups passed again with the pinned preview and section navigation on Vite/SQLite, with no browser exceptions: preview/cancel/reset, keyboard focus/Escape, custom colors/fonts/size/spacing/corners, server persistence in a fresh browser context, save failure and delayed-save handling, light/dark/system and mobile controls, account isolation, cross-tab changes and late-response/401 guards. |
| Writing, navigation and dialog browser checks | All 6 groups passed on the latest Vite/SQLite client with no browser exceptions: exact multiline send/edit and input-method-safe keys; failed-draft retention/retry; stable reading position, unread gating and latest-message jump; Cmd/Ctrl+K and honest loaded/current-channel search; command actions and shared dialog isolation/focus/Escape/return; custom dark Manrope large-type controls at 320×568 and 390×844. |
| Notification panel browser checks | All 3 groups passed against the real API: server `read:true` rendering and single-item unread/bell updates; category preference persistence through GET and a full reload; read-all persistence and bell clearing, with no browser errors. |
| Actual browser team onboarding and files | All 7 pilot stages passed with the redesigned client on Vite/SQLite: UI signup/invitation across reload, explicit join, realtime/history, failure/draft retry, authenticated file bytes, outsider denial, membership removal, mobile layout and revoking logout. |
| Chat browser regression | All 4 groups passed with the redesigned client on Vite/SQLite: independent logins/history, bidirectional realtime/deduplication, reload persistence and private-channel switching/resubscription. |
| Supporting panels and mobile layout | Checked in the actual redesigned app at 390×844, including dark appearance restoration. |
| Standalone widget host example | Passed in Chromium against the real API: actual HTML form/built bundle, authentication/history/send, no URL/browser-storage credential persistence, failed-token draft retention, widget destruction and third-party script-origin rejection. The test host used the allowed Vite origin; deployment origins still require explicit CORS configuration. |
| PostgreSQL/Compose startup and restart | Before the appearance redesign: healthy Nginx, API and PostgreSQL containers; real messages, file operations and WebSocket exchanges passed. Legacy PostgreSQL message migration passed dry-run/apply/repeated-apply checks. The redesigned client has not been rerun on this stack. |
| Separate-installation database/file/key restore | Before the appearance redesign: passed cold backup and restore into fresh projects with retained content keys, matching file bytes and unchanged source data. Automated test also refused an existing target and corrupted backup, then cleaned only its own disposable projects. |
| Dependency advisory checks | See [DEPENDENCIES.md](DEPENDENCIES.md); timestamped check, not a future guarantee |

## Reproduce application validation

With development dependencies installed from the hash-pinned lockfile:

```bash
cd backend
python -m pip install --require-hashes -r requirements-dev.lock
python -m pytest tests/ -q
```

From `frontend`:

```bash
npm ci
npm run build
node --test tests/widget.test.mjs tests/appearance.test.mjs tests/commands.test.mjs tests/api-session.test.mjs
npm run build:widget
```

Against a running disposable API on port 8000 and Vite on port 3000, run from `backend`:

```bash
python scripts/smoke_core.py
```

The smoke script creates unique users and channels, then checks real HTTP/WebSocket behavior. It must not target a live customer database. The actual browser harnesses require Python Playwright and Chromium; run from the repository root:

```bash
python frontend/tests/pilot-browser.py
python frontend/tests/chat-browser.py
python frontend/tests/appearance-browser.py
python frontend/tests/elite-browser.py
python frontend/tests/widget-browser.py
```

Their headers document endpoint/browser/bundle overrides. Use isolated accounts and storage. The [standalone widget example](../examples/widget/README.md) additionally verifies the integration path a host developer actually copies, with no permanent credentials in source or URLs.

The notification harness seeds fixtures for a unique test account. It additionally requires `ENVIRONMENT=test` and an explicit `DATABASE_URL` matching the running disposable API. After setting those, run `python frontend/tests/notification-browser.py` from the repository root.

The appearance backend tests use private databases and real bearer tokens. They check per-account ownership, fresh-session persistence, strict full-snapshot input, no row creation for default GETs, concurrent first saves and blocked/revoked access. The Node contrast checks measure selected derived color pairs; they do not establish complete WCAG conformance. Fonts are bundled with the client or supplied by the device's system stack, with no font CDN dependency.

The GitHub Actions workflow now installs the hash-locked dependencies, builds the widget and runs the smoke/browser workflow with PostgreSQL. That workflow configuration was parsed locally; no GitHub-hosted run or deployment was triggered in this task. `scripts/test-pilot.py` reproduces the disposable Docker/restore checks; see its arguments and the operations runbook before running it.

Use [OPERATIONS.md](OPERATIONS.md) for the exact Compose, backup and fresh-target restore commands. Compare encrypted message history, membership access, uploaded file bytes and live WebSocket behavior after restoration. A process PID, an open port, a zero-test run or a successful archive command alone is insufficient evidence.

## Historical baseline

The earlier access-control/stability pass on 2026-10-20 recorded **127 backend tests**, **8 widget regression tests**, frontend/widget builds, a running-service smoke check and two Chromium harnesses passing. That baseline corrected channel/socket/file/admin issues and the widget global API, but still used the old dependency/key model and API-created browser test channels. Its result is retained as history, not substituted for the current combined release checks.

## External limitations

No local check establishes a published production service, successful DNS/TLS provisioning, external user onboarding, completed third-party security review or an OTRUST proof. The supported API remains single-replica. The historical 0.4 pilot used server-readable messages and plaintext files; 0.5 adds account recovery/MFA and the separate client-encrypted channel and attachment path described above. Existing legacy channels keep their original security model. Verified organizational identity and broader widget synchronization remain separate work described in [PRODUCT_PLAN.md](PRODUCT_PLAN.md).
