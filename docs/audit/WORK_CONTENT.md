# Funktionsgranskning: dokument, filer och planering

Granskad 2026-10-23 mot produktkoden vid `c2762404` och den efterföljande,
ännu ej publicerade korrigeringen för dokumentorganisation. Tabellen skiljer
verkliga autentiserade flöden från webbplatsens lokala demo. **Implementerat**
betyder att UI, API och lagring finns inom angiven gräns; det betyder inte att en
extern leverantör eller alla driftmiljöer har certifierats.

| Utlovad eller efterfrågad förmåga | Status | Verkligt flöde och bevis | Begränsning / återstående arbete |
| --- | --- | --- | --- |
| Samarbetsdokument i kanalen | Implementerat | `WorkHub.tsx` → `SharedDocumentsPanel.tsx` → `shared-documents-api.ts` → `api/shared_documents.py` / modellerna `SharedDocument*`. Yjs-uppdateringar lagras append-only. `test_shared_documents.py`, `shared-documents.test.mjs`, befintlig `shared-documents-browser.py`. | Ren text/Markdown, inte full riktext. 50 dokument inkl. arkiverade; 50 000 tecken i texten. Osynkade ändringar finns i minnet och kräver öppet dokument. |
| Kommentarer och namngivna dokumentversioner | Implementerat | Samma produktpanel, immutable `SharedDocumentComment` / `SharedDocumentSnapshot`, författare/admin kan lösa kommentar; återställning skapar nya Yjs-ändringar. Backendtester täcker CAS/författarskap och snapshots. | Kommentarer hör till en sida, inte till ett dokumentpar eller ett markerat textspann. Ingen generell rich-text inline-kommentar. |
| Behålla ny kommentardraft medan en postning pågår | Implementerat, korrigerat i denna granskning | `SharedDocumentsPanel.addComment` fångar inskickad text och rensar bara om den aktuella draften fortfarande är samma. Ny verklig browserregression fördröjer POST och skriver en andra draft. | Inget löfte om beständig offline-kommentardraft över omladdning. |
| Arkivera/återställa och lokal Markdown-export | Implementerat | Versionsskyddad author/admin-arkivering i `api/shared_documents.py`, browserlokal export i panelen. `test_shared_documents.py` och befintlig tvåanvändar-browserproof. | Arkivering är inte permanent radering; retention är ett separat ägarflöde. |
| Riktiga dokumenthögar, skapa/byta namn/radera tom hög | Implementerat för native, tillagt i denna granskning | `SharedDocumentOrganization.tsx` → `document-organization-api.ts` → `api/document_organization.py` → fyra additiva FK-tabeller inklusive sparade källreferenser. Stacktitlar är purpose-krypterade. `test_document_organization.py`, klienttest och ny faktisk browserproof. | Gäller delade FreeCollab-sidor i en kanal, inte binärfilsmappar eller extern leverantörsorganisation. 30 högar. |
| Flytta sida med tangentbord/drag och ångra utan textförlust | Implementerat för native, tillagt i denna granskning | Egen placementversion/CAS, flyttkontroll och drag i produktpanelen. Undo skriver bara placement, inte Yjs-text. Ny regression redigerar text efter flytt och testar en annan medlems konkurrerande flytt. | Senaste Undo är lokalt för den öppna organisationspanelen. En senare flytt eller ersatt destinationshög avvisar gammal Undo med 409. |
| Gamla stackoperationer ska inte träffa en återskapad hög | Implementerat, tillagt i denna granskning | Servergenererad `generation`, versions- och generationstoken på rename/delete och generation på destination för move/Undo. Test återanvänder samma ID med ny generation och verifierar avslag. | Klienter ska skapa nytt UUID per ny resurs och behålla ID för exakt retry. Ingen allmän modell för att återuppliva raderade resurser. |
| Dokumentkopplingar och synliga förslag | Implementerat för native, tillagt i denna granskning | Explicit bidirektionellt par, kanonisk unikhetskontroll, öppna relaterad sida, ta bort koppling. Förslag använder gemensamma ord i aktuell titel eller sidans synliga olösta kommentarer mot andra synliga titlar, visar ursprungskommentar som evidens och skriver först efter Spara. UI→API→DB och tests med kanal-, arkiv- och medlemskontroll. | Titelsamband är inte en AI-slutsats eller bekräftat sakförhållande. Ingen dold sökning, kroppsskanning eller automatisk länkmutation. Max 200 par. |
| Bevara faktiskt meddelande i en dokumenthög | Implementerat för native, tillagt i denna granskning | Faktisk Chat-action → WorkHub Documents → serverhämtad message-preview → explicit val/Spara → `SharedDocumentStackReference`. Samma kanal, aktuell textdigest och destinationsgeneration kontrolleras före write. Källa öppnar originalmeddelande/tråd; backendtester täcker ändrad/borttagen/felkanalskälla samt idempotens. | Ingen textkopia; excerpt är aktuell när den hämtas. Max 200 referenser/kanal. Secure unsupported. En källradering lämnar en borttagbar unavailable-referens. |
| Automatisk koppling av kommentarer till högar | Implementerat som faktisk härledd produktvy, tillagt i denna granskning | Organisationens authenticated GET joinar nuvarande sidplacering med verkliga sidkommentarer. Stackvy visar originaldokument/författare och öppnar/fokuserar kommentaren. Flytt och arkivfilter ändrar association utan kopia. API- och faktisk browserregression. | Högst100 senaste kommentarer i aktuell dokumentvy, med incomplete-notis. Ingen fristående comment→document-pair-sidecar eller textspannsankare. |
| AI-hjälp direkt i en riktig native-hög | Implementerat som granskat readonly-flöde, tillagt i denna granskning | `StackCopilot` i `SharedDocumentOrganization.tsx` återanvänder riktiga `ContextualCopilot`, current same-stack SharedDocument-källor, valbara upp till8, synligt urval och24k-limit. Stackgeneration/membership/placement/sourceversion/locale binds i contextKey. Separat aktuell faktisk privat TLS/browserproof granskar exakt valt innehåll. | Kräver konfigurerad verklig provider och explicit disclosure/send. Endast sammanfattning/läsordning/frågor; inget automatiskt move/link/apply. Kommentarer och message-refs skickas inte tyst som dokumentkälla. |
| Autonom semantisk/LLM-parning av kommentarer, dokument och högar | Saknas som autonom motor | Dagens automatiska gruppering är placementhärledd, och förslag bygger på förklarade ordregler med explicit Spara. Riktiga AI-svar är granskade och read-only. | Får inte säljas som autonom betydelseförståelse, automatisk reparenting eller dold AI-analys av alla kommentarer. |
| Dokumentorganisation i E2EE-kanaler | Saknas | Nytt server-409 för både preparing/active secure; klienten visar uttrycklig begränsning och skickar inga organisationstitlar. Backend- och klienttester verifierar fail-closed. | Kräver ett eget verifierat Matrix-eventprotokoll och opaque index. Befintliga krypterade sidor/kommentarer/versioner fungerar fortsatt som platt lista. Ingen plaintextfallback. |
| Publik demos högar/kopplingar | Implementerat som lokal demo | `DemoDocumentDesk.tsx`, `DemoConnections.tsx`, `demo-showroom.ts`; reducer-/browsertester. | Reload återställer lokala exempel. Demons reducers är inte lagring, backend eller bevis för E2EE-organisation. Den nya autentiserade native-funktionen ovan är en separat implementation. |
| Filer: uppladdning, kö, avbryt/omförsök och nedladdning | Implementerat | `ChannelTools` → `ChannelFiles.tsx` → fil-API och lagring. `test_file_experience.py`, filklienttester och dokumenterad isolerad browserharness. | Native filbytes lagras utan applikationens innehållskryptering; UI upplyser om det. Matrix-bilagor använder klient-E2EE. Extern objektlagrings drift/backup granskas separat. |
| Filversioner, galleri, förhandsvisningar och versionskommentarer | Implementerat | `FilePreview.tsx`, file-experience-API, immutable fileversioner och versionsbunden kommentar. `FILE_EXPERIENCE.md`, `test_file_experience.py`. | Passiva begränsade previews; ingen godtycklig aktiv HTML/makro eller fjärrrendering. Kommentaren hör till exakt filversion. |
| Office-filer i webbläsaren | Implementerat för passiv native-preview | Bunden OOXML-textutvinning för DOCX/XLSX/PPTX, zip-/XML-/storlekskontroller. Backendregressioner för felaktiga paket och gränser. | Ingen Office Online/WOPI, full sidlayout eller webbsamredigering. Secure Office-preview är nedladdning till en betrodd app. |
| Redigera Office-fil i installerad app | Implementerat protokoll; extern användaracceptans overifierad | Native WebDAV-handoff efter password/MFA, tidsbunden capability, GET/HEAD/LOCK/PUT med versions- och medlemskontroller samt immutable nya versioner. `test_file_experience.py`, `FILE_EXPERIENCE.md`. | Riktiga Word/Excel/PowerPoint-binärer och olika OS-versioner är inte certifierade av fixtures. Inte E2EE-handoff. Se integrationsgranskningen för leverantörsgränser. |
| Högar/mappar för binära och externa filer | Saknas som generell produktfunktion | Produktens nya stack-FK pekar uttryckligen på `SharedDocument`, inte `FileDocument` eller externa filer. | Får inte säljas som full filhanterare eller generell dokumenttaxonomi. Externa molnfilsväljare beskrivs i `INTEGRATIONS.md`. |
| Projekt och uppgifter | Implementerat | `WorkHub` / embed → `ProjectTasksPanel.tsx` → `api/project_tasks.py` → `ProjectBoard`, `TaskCard`, immutable `ProjectTaskRevision`. `test_project_tasks.py`, klient- och browserproof i `PROJECT_TASKS.md`. | Kanalbundna projekt, högst 20 projekt/1 000 uppgifter inkl. arkiverade. Status, ansvarig, deadline, prioritet, 40 checklistpunkter; inte full portfölj-/Gantt-/resursplanering. |
| Versionskonflikt och native/secure uppgiftsfält | Implementerat | Native purpose-krypterade revisionsfält, secure verifierade Matrix-events med opaque serverreferens. UI behåller draft vid 409 och kräver explicit Reload current revisions. | Fält får inte läsas/skrivas som native i secure. Generisk task-from-message i showroom är inte automatiskt en serverlagrad källa; verklig AI-handoff kräver separat granskat Save-flöde. |
| Uppgiftslänkar till fil och beslut | Implementerat | Upp till 20 file IDs och 20 decision IDs; klick hämtar aktuellt auktoriserad fil/beslutsdetalj. `ProjectTasksPanel.tsx`, `PROJECT_TASKS.md`, backendtester. | Ingen allmän task→shared-page-relationsmodell eller dokumentkopplingskommentar. |
| Dokumentlänkar till externa leverantörer | Implementerat för referenser | `DocumentLinks.tsx` → `integrations_content.py`; allowlistade HTTPS-länkar, native-kryptering eller Matrix-event. `test_document_calendar_integrations.py`, `document-calendar.test.mjs`. | Länk öppnar leverantören utan att ändra dess ACL. Ingen automatisk OAuth-/innehållsimport eller sökindex följer av länken. Se `INTEGRATIONS.md`. |
| Kanalkalender och tidszon/all-day | Implementerat | `CalendarPanel.tsx` → `document-calendar-api.ts` → immutable `IntegrationContentRevision`; native/Matrix-gräns. Kalender- och dokumentintegrationstester. | En kanal är kalenderns resursgräns. Ingen fristående privat kalenderresurs. |
| Återkommande händelser, enstaka undantag, intern RSVP | Implementerat | `CalendarPlanning` / `calendar_planning.py`, ändliga dag/vecka/månad-regler, flytta/avbryt förekomst, current-revision svar för egen inbjudan. `test_calendar_planning.py`, faktisk isolerad browserharness. | Begränsade regler, inte godtycklig iCalendar-RRULE. Interna deltagare är nuvarande kanalmedlemmar; extern emailinbjudan kräver annat godkänt flöde. |
| Hitta gemensam tid | Implementerat inom vald kanal | Native busy-beräkning eller lokal Matrix-expansion; API svarar `private_calendars_included:false`. `CALENDAR_PLANNING.md`, tillgänglighetstester. | Ingen komplett personlig free/busy: övriga kanaler, privata och externa kalendrar ingår inte. UI ber användaren kontrollera andra åtaganden. |
| Personlig samlad agenda | Implementerat som My Day | `MyDay.tsx` → `/my-day` → `services/my_day.py`; aktuellt tillåtna kanaler över arbetsytor, klientexpanderade kommande händelser. `test_my_day.py`, `MY_DAY.md`. | Kanalhändelser visas som kontext; separat privat kalender-CRUD, heltäckande personlig tillgänglighet och privat providerkalenderskanning saknas. Gränser redovisar ofullständig snapshot. |
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
