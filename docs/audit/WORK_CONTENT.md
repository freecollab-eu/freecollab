# Funktionsgranskning: dokument, filer och planering

**Aktuell komplettering: 2026-10-25, granskningsgrenen `design/open-studio`.** Äldre commit-/datumhänvisningar nedan avser den ursprungliga granskningen. Statusraderna ovanför historikavsnitten har uppdaterats för faktisk ny kod; [validering](../VALIDATION.md) skiljer körd källa från kundens externa acceptans.

Granskad 2026-10-23 mot produktkoden vid `c2762404` och den efterföljande,
ännu ej publicerade korrigeringen för dokumentorganisation. Tabellen skiljer
verkliga autentiserade flöden från konfigurationsförberedelser och extern acceptans. **Implementerat**
betyder att UI, API och lagring finns inom angiven gräns; det betyder inte att en
extern leverantör eller alla driftmiljöer har certifierats.

| Utlovad eller efterfrågad förmåga | Status | Verkligt flöde och bevis | Begränsning / återstående arbete |
| --- | --- | --- | --- |
| Samarbetsdokument i kanalen | Implementerat | `WorkHub.tsx` → `SharedDocumentsPanel.tsx` → `shared-documents-api.ts` → `api/shared_documents.py` / modellerna `SharedDocument*`. Yjs-uppdateringar lagras append-only. `test_shared_documents.py`, `shared-documents.test.mjs`, befintlig `shared-documents-browser.py`. | Ren text/Markdown, inte full riktext. 50 dokument inkl. arkiverade; 50 000 tecken i texten. Osynkade ändringar finns i minnet och kräver öppet dokument. |
| Kommentarer och namngivna dokumentversioner | Implementerat | Befintlig kommentar-/snapshot-lagring kompletteras med validerade Yjs-relative positioner och faktisk vald text. `document-anchors.ts`, backend opaque-validation och Yjs-regressioner. | Ändrad eller borttagen text kan göra ett ankare otillgängligt. Kommentaren och historiken finns kvar; ingen gissad ny textplats. |
| Behålla ny kommentardraft medan en postning pågår | Implementerat, korrigerat i denna granskning | `SharedDocumentsPanel.addComment` fångar inskickad text och rensar bara om den aktuella draften fortfarande är samma. Ny verklig browserregression fördröjer POST och skriver en andra draft. | Inget löfte om beständig offline-kommentardraft över omladdning. |
| Arkivera/återställa och lokal Markdown-export | Implementerat | Versionsskyddad author/admin-arkivering i `api/shared_documents.py`, browserlokal export i panelen. `test_shared_documents.py` och befintlig tvåanvändar-browserproof. | Arkivering är inte permanent radering; retention är ett separat ägarflöde. |
| Riktiga dokumenthögar, skapa/byta namn/radera tom hög | Implementerat för native, tillagt i denna granskning | `SharedDocumentOrganization.tsx` → `document-organization-api.ts` → `api/document_organization.py` → fyra additiva FK-tabeller inklusive sparade källreferenser. Stacktitlar är purpose-krypterade. `test_document_organization.py`, klienttest och ny faktisk browserproof. | Placeringar för sidor kompletteras med samlingsreferenser för andra materialfamiljer; leverantörens originalorganisation ändras inte. 30 högar. |
| Flytta sida med tangentbord/drag och ångra utan textförlust | Implementerat för native, tillagt i denna granskning | Egen placementversion/CAS, flyttkontroll och drag i produktpanelen. Undo skriver bara placement, inte Yjs-text. Ny regression redigerar text efter flytt och testar en annan medlems konkurrerande flytt. | Senaste Undo är lokalt för den öppna organisationspanelen. En senare flytt eller ersatt destinationshög avvisar gammal Undo med 409. |
| Gamla stackoperationer ska inte träffa en återskapad hög | Implementerat, tillagt i denna granskning | Servergenererad `generation`, versions- och generationstoken på rename/delete och generation på destination för move/Undo. Test återanvänder samma ID med ny generation och verifierar avslag. | Klienter ska skapa nytt UUID per ny resurs och behålla ID för exakt retry. Ingen allmän modell för att återuppliva raderade resurser. |
| Dokumentkopplingar och synliga förslag | Implementerat för native, tillagt i denna granskning | Explicit bidirektionellt par, kanonisk unikhetskontroll, öppna relaterad sida, ta bort koppling. Förslag använder gemensamma ord i aktuell titel eller sidans synliga olösta kommentarer mot andra synliga titlar, visar ursprungskommentar som evidens och skriver först efter Spara. UI→API→DB och tests med kanal-, arkiv- och medlemskontroll. | Titelsamband är inte en AI-slutsats eller bekräftat sakförhållande. Ingen dold sökning, kroppsskanning eller automatisk länkmutation. Max 200 par. |
| Bevara faktiskt meddelande i en dokumenthög eller samling | Implementerat native och enhetskrypterat | Chattens meddelandeåtgärd → WorkHub Documents → källförhandsgranskning → explicit val/Spara. Native använder `SharedDocumentStackReference`; krypterade rum använder verifierade meddelandekällor och opaka organisationsrevisioner på enheten. Typade samlingar har även **Lägg till material**. Källa öppnar originalmeddelande/tråd. | Ingen textkopia; samma kanal, granskad textdigest och destinationsgeneration kontrolleras före write. Högst 200 sparade meddelandereferenser/kanal samt separat begränsat krypterat eventformat. En källradering lämnar en borttagbar unavailable-referens. |
| Automatisk koppling av kommentarer till högar | Implementerat som faktisk härledd produktvy, tillagt i denna granskning | Organisationens authenticated GET joinar nuvarande sidplacering med verkliga sidkommentarer. Stackvy visar originaldokument/författare och öppnar/fokuserar kommentaren. Flytt och arkivfilter ändrar association utan kopia. API- och faktisk browserregression. | Högst100 senaste kommentarer i aktuell dokumentvy, med incomplete-notis. Textspannsankare använder verifierade relativa Yjs-positioner; ingen gissad comment→document-pair-sidecar. |
| AI-hjälp direkt i en riktig native-hög | Implementerat som granskat readonly-flöde, tillagt i denna granskning | `StackCopilot` i `SharedDocumentOrganization.tsx` återanvänder riktiga `ContextualCopilot`, current same-stack SharedDocument-källor, valbara upp till8, synligt urval och24k-limit. Stackgeneration/membership/placement/sourceversion/locale binds i contextKey. Separat aktuell faktisk privat TLS/browserproof granskar exakt valt innehåll. | Kräver konfigurerad verklig provider och explicit disclosure/send. Endast sammanfattning/läsordning/frågor; inget automatiskt move/link/apply. Kommentarer och message-refs skickas inte tyst som dokumentkälla. |
| Semantiska modellförslag för kommentarer, dokument och högar | Implementerat med separat tillämpning | Semantiska aktuella källor → varaktig AI-gruppering → granskad skrivning av verkliga samlingsreferenser; [AI-ytor](../AI_SURFACES.md). | Inget automatiskt reparenting eller dold analys. Source-version, valda ID:n och katalog kontrolleras igen innan tillämpning. |
| Dokumentorganisation i E2EE-kanaler | Implementerat | Enhetskrypterad organisationsadapter, opak eventauktoritet, verifierade publiceringsroller, konfliktgranskning samt legacy/typed peer-dokument. [Krypterat arbete](../ENCRYPTED_WORK.md). | Serverns native-organisationsrutter fortsätter neka plaintext. Behörighet vid publicering kan inte ersättas med en påhittad klientroll. |
| Publik demos högar/kopplingar | Samma produkt med isolerade exempeldata | Ordinarie `App`, `SharedDocumentsPanel`, organisations-API och databas, enligt [produktdemon](../PRODUCT_DEMO.md). | Gamla lokala showroom-reducers är historiska förhandsvisningar och definierar inte dagens produktdemo. Externa anslutningar kräver egen installation. |
| Filer: uppladdning, kö, avbryt/omförsök och nedladdning | Implementerat | `ChannelTools` → `ChannelFiles.tsx` → fil-API och lagring. `test_file_experience.py`, filklienttester och dokumenterad isolerad browserharness. | Native filbytes lagras utan applikationens innehållskryptering; UI upplyser om det. Matrix-bilagor använder klient-E2EE. Extern objektlagrings drift/backup granskas separat. |
| Filversioner, galleri, förhandsvisningar och versionskommentarer | Implementerat | `FilePreview.tsx`, file-experience-API, immutable fileversioner och versionsbunden kommentar. `FILE_EXPERIENCE.md`, `test_file_experience.py`. | Passiva begränsade previews; ingen godtycklig aktiv HTML/makro eller fjärrrendering. Kommentaren hör till exakt filversion. |
| Office-filer i webbläsaren | Implementerat för passiv native-preview | Bunden OOXML-textutvinning för DOCX/XLSX/PPTX, zip-/XML-/storlekskontroller. Backendregressioner för felaktiga paket och gränser. | Ingen Office Online/WOPI, full sidlayout eller webbsamredigering. Secure Office-preview är nedladdning till en betrodd app. |
| Redigera Office-fil i installerad app | Implementerat protokoll; extern användaracceptans overifierad | Native WebDAV-handoff efter password/MFA, tidsbunden capability, GET/HEAD/LOCK/PUT med versions- och medlemskontroller samt immutable nya versioner. `test_file_experience.py`, `FILE_EXPERIENCE.md`. | Riktiga Word/Excel/PowerPoint-binärer och olika OS-versioner är inte certifierade av fixtures. Inte E2EE-handoff. Se integrationsgranskningen för leverantörsgränser. |
| Samlingar för binära och externa filer | Implementerat | `ContentCollections` och `ContentCollectionItem` binder elva kanoniska materialfamiljer till en verklig hög; krypterade rum använder opaka verifierade referenser. | Originalet och leverantörens ACL behålls. Samlingen är inte en ny filkopierings- eller leverantörsbehörighet. |
| Projekt och uppgifter | Implementerat | `WorkHub` / embed → `ProjectTasksPanel.tsx` → `api/project_tasks.py` → `ProjectBoard`, `TaskCard`, immutable `ProjectTaskRevision`. `test_project_tasks.py`, klient- och browserproof i `PROJECT_TASKS.md`. | Kanalbundna projekt, högst 20 projekt/1 000 uppgifter inkl. arkiverade. Status, ansvarig, deadline, prioritet, 40 checklistpunkter; inte full portfölj-/Gantt-/resursplanering. |
| Versionskonflikt och native/secure uppgiftsfält | Implementerat | Native purpose-krypterade revisionsfält, secure verifierade Matrix-events med opaque serverreferens. UI behåller draft vid 409 och kräver explicit Reload current revisions. | Fält får inte läsas/skrivas som native i secure. Generisk task-from-message i showroom är inte automatiskt en serverlagrad källa; verklig AI-handoff kräver separat granskat Save-flöde. |
| Uppgiftslänkar till fil och beslut | Implementerat | Upp till 20 file IDs och 20 decision IDs; klick hämtar aktuellt auktoriserad fil/beslutsdetalj. `ProjectTasksPanel.tsx`, `PROJECT_TASKS.md`, backendtester. | Ingen allmän task→shared-page-relationsmodell eller dokumentkopplingskommentar. |
| Dokumentlänkar till externa leverantörer | Implementerat för referenser | `DocumentLinks.tsx` → `integrations_content.py`; allowlistade HTTPS-länkar, native-kryptering eller Matrix-event. `test_document_calendar_integrations.py`, `document-calendar.test.mjs`. | Länk öppnar leverantören utan att ändra dess ACL. Ingen automatisk OAuth-/innehållsimport eller sökindex följer av länken. Se `INTEGRATIONS.md`. |
| Kanalkalender och tidszon/all-day | Implementerat | `CalendarPanel.tsx` → `document-calendar-api.ts` → immutable `IntegrationContentRevision`; native/Matrix-gräns. Kalender- och dokumentintegrationstester. | Delade händelser hör till sin kanal. Den separata ägarbundna privata kalendern beskrivs nedan och i [PERSONAL_CALENDAR](../PERSONAL_CALENDAR.md). |
| Återkommande händelser, enstaka undantag, intern RSVP | Implementerat | `CalendarPlanning` / `calendar_planning.py`, ändliga dag/vecka/månad-regler, flytta/avbryt förekomst, current-revision svar för egen inbjudan. `test_calendar_planning.py`, faktisk isolerad browserharness. | Begränsade regler, inte godtycklig iCalendar-RRULE. Interna deltagare är nuvarande kanalmedlemmar; extern emailinbjudan kräver annat godkänt flöde. |
| Hitta gemensam tid | Implementerat inom vald kanal | Native busy-beräkning eller lokal Matrix-expansion; API svarar `private_calendars_included:false`. `CALENDAR_PLANNING.md`, tillgänglighetstester. | Ingen komplett personlig free/busy: övriga kanaler, privata och externa kalendrar ingår inte. UI ber användaren kontrollera andra åtaganden. |
| Personlig agenda och separat privat kalender | Implementerat | `MyDay` och `/calendar` → `PersonalCalendar` → ägar-/arbetsytescoped purpose-krypterad CRUD/CAS, recurrence och ICS-export. [Privat kalender](../PERSONAL_CALENDAR.md). | Kanalhändelser och egna privata händelser är separata. Ingen tyst providerkalenderskanning. |
| ICS-import/export och kalenderpåminnelse | Implementerat inom dokumenterade gränser | Lokalt granskad ICS och begränsad recurrence/EXDATE; DISPLAY-alarm i ICS. `DOCUMENTS_CALENDAR.md`, klient/API-tester. | Export är en kopia, inte synk. ICS-alarm aktiveras av användarens kalenderapp; det är inte native browser-push för alla kalenderhändelser. |
| Externa inbjudningar, provider-RSVP och ICS-prenumeration | Implementerad kod / extern acceptans overifierad | Granskad kö, villkorliga vendorformat, native-only revokerbar feed. `CALENDAR_DELIVERY.md`; separata aktuella resultat och luckor i `INTEGRATIONS.md`. | Kundens Google/Microsoft OAuth, drift och verklig leverans måste verifieras externt. Ingen leveransgaranti följer av HTTPS-fixtures. |
| Ägarexport och säker retention | Implementerat inom native-gräns | `DataControlsPanel.tsx` → `api/data_control.py` → worker `services/data_control.py`, verifierad krypterad arkivfil, exakt granskat purge-set, FK-ordning och fysiska filraderingsjobb. `test_data_controls.py`, `test_data_controls_review.py`; nya stack/metadata-workerregressioner. | Disabled/hold som default. Export kan vara läsbar när ägaren hämtar den. Matrixkopior, externa leverantörer, enheter och backups raderas inte av native-retention. |
| Nya organisationens export/radering och aktuell kanalprofil | Implementerat, tillagt i denna granskning | Verifierade stacktitlar inkl. generation, placements/par och aktuell channelmetadata i explicit exportallowlist. Nya workerregressioner med SQLite-FK ON; ny relation skyddar båda gamla sidorna. | Aktuell kanalprofil hålls kvar med Channel-identiteten, inte tidsraderas som historiskt innehåll; detta bevarar monoton CAS. Identitetsstängning behåller opak författarstub. Se `IDENTITY_SECURITY.md`. |

## Vad som faktiskt korrigerades

Före granskningen fanns högar och dokumentpar bara i den lokala demon. Den nya
native-produktimplementationen har riktig behörighetskontroll, krypterad titel,
beständig lagring, generation+CAS, revisionsskyddad Undo, explicita relationer samt
export och retention. Riktiga message→stack-referenser och härledda kommentarer
finns nu också, med originalkällor, aktuella behörigheter och explicit sparande. Ett separat fel kunde radera en nyare kommentardraft efter
ett långsamt POST-svar; det är korrigerat utan att låsa användarens skrivfält.

En föreslagen tidsradering av aktuell channelmetadata stoppades i granskningen:
den skulle återställa versionsräknaren och kunna acceptera en gammal editor.
Kanalidentitet och dess monotona revision behålls nu. För stackar används i stället
en separat immutable creation-generation, så ett uttryckligen raderat och sedan
återskapat ID inte kan träffas av en gammal flytt eller namnändring.

## Verifieringsprotokoll för denna ändring

Aktuella körresultat skrivs i arbetsytans privata
`.freecollab/proof-results/feature-audit-20261023/` med etiketten
`document-organization-reference-final` (69/69 PASS) samt `document-organization-browser`.
Klient, befintliga dokumentklienttester och i18n: 15/15 PASS. TypeScript: PASS.
Slutlig verklig organisationskörning: **15/15 PASS**, med **19/19 aktuella
källhashar** mot slutlig Chat-källa `6c80b637`. Alla14 tidigare grupper behålls,
och en ny grupp sparar en faktisk trådsvarsreferens, öppnar originalsvaret och
kontrollerar att både trådens exakta oskickade draft och huvudcomposern finns kvar.
Övriga grupper omfattar verklig drag/flytt, metadata-Undo efter textredigering,
konkurrerande flytt, förklarade dokumentkopplingar, originalkommentarers
flytt/arkiv/källfokus, långsamt kommentar-POST, huvudmeddelandets källa och svensk
320px utan overflow. Rapport: `document-organization-browser.json`; skärmbild med
samma filnamn och `.png`.

Stackens riktiga privata TLS-providerflöde: **15/15 PASS** tillsammans med övriga
Copilot-grupper, med **26/26 aktuella källhashar**. En vald Yjs-sida når den
syntetiska HTTPS-providern efter explicit disclosure; en ovald sida, kommentarer
och meddelanden ingår inte, och inga stack-/dokument-/uppgiftsmutationer sker.
Rapport: `copilot-browser.json`. Dessa resultat gäller isolerade faktiska
produktflöden och privata syntetiska tjänster; de certifierar ingen extern
leverantör, faktisk kundmiljö eller E2EE-organisation.
Katalogerna innehåller syntetiska privata fixtures och resultat, inte kundnycklar.
Det nya native-flödet ska inte räknas som publicerat innan ordinarie CI och
releasesteg är klara. Gamla browserharnessar är läsbara testbevis på täckning,
inte ett påstående om att samtliga har körts om i denna ändring.
