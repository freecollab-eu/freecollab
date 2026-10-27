# FreeCollab: funktioner och implementation

Kompletterad **2026-10-27** med de naturliga samarbetsflödena i
`feature/natural-collaboration-flow`, efter den tidigare granskningen av
`design/open-studio`. Den här
förteckningen beskriver produktkod i grenen. Den ändrar inte status för `main`,
en publicerad version eller en redan driftsatt Railway-instans.

FreeCollab är en **egen installation** på dator, server, lokalt nätverk, VPN
eller valfri hosting. Kundens identitet, lagring, nycklar och anslutningar hör
till den installationen. En central FreeCollab-tjänst krävs inte. Se
[lokal drift](SELF_HOSTING.md) och [anslutningsguiden](CONNECTION_SETUP.md).

**Implementerat** betyder en nåbar produktväg med faktisk lagring, transport
eller klientexekvering. **Kundkonfiguration** betyder att operatören måste ange
sin egen tjänst, behörighet eller signeringsidentitet. **Extern acceptans**
betyder exempelvis ett riktigt kundkonto, fysisk enhet eller kapacitetsprov.
Protokolltester ersätter inte dessa senare kontroller.

## Naturliga samarbetsflöden

Markerad meddelandetext blir ett granskat uppgiftsutkast. Förstorade dokument
behåller sin samtalsremsa; `/` och `@` ordnas efter träff, kontext och
sessionshistorik. En läsbunden återkomstöversikt länkar till verkliga källor.
Dokument kan få ett lokalt högförslag vid ordnande och synlig Ångra för flytt,
högens namn och en ny tom hög. Se [kontrakt och gränser](NATURAL_COLLABORATION.md),
inklusive begränsad historik, versionsskydd och krypterade källänkar.

## Produktens sammanhängande flöden

| Område | Implementerad produktväg | Kontrakt och gräns |
| --- | --- | --- |
| Samtal, trådar, historik, DM och grupper | Senaste meddelandet längst ned, kvitton, bevakning, reaktioner, redigering, omnämnanden, schemaläggning och påminnelser. | [Kommunikation och klienter](audit/COMMUNICATION_CLIENTS.md). Schemalagd E2EE-leverans behöver den betrodda enheten. |
| `/` och `@` i arbetet | Gemensam sökbar funktionslista och valda person-, grupp- och material-ID:n i chatt, trådar, dokument, kommentarer och övriga författarfält. Installerade appkommandon använder samma ingång. | [Samarbetskontrakt](UNIVERSAL_COLLABORATION.md), [kommandon](SLASH_COMMANDS.md), [referenser](MENTIONS_REFERENCES.md). Öppning publicerar inget. Konfiguration och identifierare får inga låtsasreferenser. |
| Flera arbetsytor samtidigt | Dokument, uppgifter, beslut, appgranskningar och övrigt arbete ligger kvar i samtalet. Delad, förstorad och minimerad vy bevarar editor, utkast och session. | [Samtalsflödet](FLOW_FIRST_UX.md), [vylägen](WORKSPACE_MODES.md). Verklig stängning och byte av behörighet har skrivskydd. |
| Dokument, högar och allt material | Yjs-sidor, historik, återställning, textankrade kommentarer, placeringar, relationer och samlingar av dokument, filer, länkar, meddelanden, uppgifter, projekt, beslut, kalender, klipp, flödesposter och workflows. | [Dokument](SHARED_DOCUMENTS.md), [organisation](DOCUMENT_ORGANIZATION.md), [innehåll](audit/WORK_CONTENT.md). Referenser flyttar inte originalet eller ger ny åtkomst. |
| Sökning och privat kalender | Beständigt semantiskt LSA-index, inkrementell uppdatering, aktuell källkontroll och valbar OpenSearch-adapter. Privat kalender med eget CRUD, versionering, återkommande händelser och ICS-export. | [Sökning](ENTERPRISE_SEARCH.md), [privat kalender](PERSONAL_CALENDAR.md). E2EE-sökning körs på enheten. Privat kalender delas inte genom kanalmedlemskap. |
| AI där innehållet finns | Skrivhjälp och granskad läshjälp i författarfält, dokument, uppgifter, feed, beslut, filer/PDF-utdrag, kalender, länkar, klipp, workflows och krypterat arbete. Semantiska strukturförslag granskas före tillämpning. | [AI-ytor](AI_SURFACES.md), [Copilot](COPILOT.md). Källa, texturval, mottagare, modell och aktuell behörighet binds till varje granskning. |
| Inbyggd AI-orkestrering | Varaktiga runs/steps, återhämtning, cancel/resume, begränsad modellplanering och handoff, flera profiler/routing, personliga nycklar, användningsbokföring och budgetreservationer. Gemensam verklig exekveringshistorik. | [AI-runtime](AI_RUNTIME.md), [orkestrering](AI_ORCHESTRATION.md). Kostnad är uppskattad om provider saknar kvitto; budgeten ersätter inte leverantörens fakturering. |
| AI-leverantörer och kunskap | OpenAI-/xAI-kompatibelt, Anthropic, Azure, Gemini och Ollama, inklusive uttryckligen tillåtna privata ursprung. Workspace-RAG och registrerade HTTPS-läs-/sökverktyg med separat disclosure. | [AI-runtime](AI_RUNTIME.md). Kundens egna modeller och nycklar. Modellen får inte välja godtycklig kod eller en oregistrerad URL. |
| Appar, workflows och Slack | Versionsbundna appargument, separat granskning och outbox; appsteg i workflows; löpande Slack-in-/utflöde, trådar, deduplicering, paus/återkallelse och leveranskvitton. | [Appplattform](APP_PLATFORM.md), [workflows](WORKFLOWS.md), [Slack-koppling](SLACK_LIVEBRIDGE.md). Okänd extern leverans återförsöks inte tyst. Krypterade appanrop kräver särskild förgrundsgranskning. |
| Möten, inspelning och klipp | WebRTC, valbar kundägd SFU, inspelningssegment efter alla deltagares samtycke, kontinuerliga lokala undertexter, privata återhämtningsutkast och samtyckesbunden AI-sammanfattning. | [Möten](MEETINGS.md). Inspelningen körs på värdens betrodda enhet; nya deltagare eller återkallelse stoppar segmentet. |
| Krypterat och federerat arbete | Verifierade Matrix-enheter och opaka index för typat gemensamt arbete: dokument, projekt, uppgifter, beslut, flöde, kalender, samlingar, workflows och mötesflöden. | [Krypterat arbete](ENCRYPTED_WORK.md), [federation](FEDERATED_WORK.md), [Connect](CONNECT_FEDERATION.md). Lokala konton, privata kalendrar, hemligheter och operatörsinställningar behåller sin egen auktoritet. |
| Kundens anslutningar och inbäddning | Kundägd Microsoft 365 och Google, SSO/SCIM, Office-/filanslutningar, SDK, React och Web Components. | [Integrationer](audit/INTEGRATIONS.md), [identitet](audit/IDENTITY_SECURITY.md), [SDK](COMPONENT_SDK.md). Registrering och samtycke sker i kundens miljö. |
| Webb, desktop och mobil | Samma produkt, offline-/utköflöden, signerad desktopuppdatering med hälsokontroll/rollback och aktuell behörighetskontrollerad resursnavigering. Android/iOS har dessutom verifierade app-/universallänkar från operativsystemet. | [Klienter](NATIVE_CLIENTS.md), [kommunikationsgranskning](audit/COMMUNICATION_CLIENTS.md). Desktop använder webb-/browsernavigering, utan native HTTPS-domänassociation. Kundsignering, mobil domänverifiering och fysisk OS-acceptans är separata. |
| Personlig form och språk | Skandinavisk rumsvy, egna färger/typsnitt, mobil layout och svenska/engelska. Profilbilder kommer från riktiga uppladdade kontobilder, med initialer när bild saknas. | [Designsystem](DESIGN_SYSTEM.md), [utseende](APPEARANCE.md), [språk](LANGUAGES.md). Användbarhetsstudier ersätts inte av kodtester. |
| Data, drift och säkerhet | Export/radering/retention omfattar de nya privata kalendrarna, AI-körningarna, samlingsreferenserna, samtycken, appkvitton och Slack-kopplingarna. Driftpanelen läser verkliga köer och heartbeats. | [Datakontroller](DATA_CONTROLS.md), [drift](audit/SDK_OPERATIONS.md). Anonym numerisk AI-bokföring behålls så att radering inte återställer en förbrukad budget. |

## De tidigare konkreta kodluckorna

De fem tidigare uppräknade områdena har fått faktiska implementationer, inte
bara framtidsplaner. Detaljerad spårning finns i
[AI/appar/workflows](audit/AI_WORKFLOWS.md),
[innehåll](audit/WORK_CONTENT.md) och
[kommunikation/klienter](audit/COMMUNICATION_CLIENTS.md).

Den nya koden granskas även tillsammans: ett verkligt appkommando från huvudchatt,
tråd eller dokument ska öppna samma granskning och behålla källutkastet; en sökträff
eller applänk ska öppna rätt aktuella resurs; återkallat inspelningssamtycke ska
stoppa både native publicering och krypterade återförsök. Läs
[valideringsloggen](VALIDATION.md) för vilken källa som faktiskt körts.

## Demo, aktivering och leverans

[Produktdemon](PRODUCT_DEMO.md) använder **samma appkomponenter och API** med
isolerade tidsbegränsade exempelkonton. Kalender, samlingar, sök och samtyckesstyrda
native möten använder ordinarie produktvägar. Konton från olika besök får inte
dela innehåll eller mötesbehörigheter. Utgångna besöks nya tabeller rensas genom
samma FK-kontrollerade livscykel.

En publik exempelinstallation innehåller inga kundnycklar eller externa
modellanslutningar. Operatörskonfiguration och externa disclosure-handlingar är
därför spärrade där. Det är ett konfigurationsval i den vanliga produkten, utan
en separat demonstrationseditor eller påhittade providersvar.

Riktig kund-OAuth/SSO, Office-program, appbutikssignering, fysisk mobilringning,
DNS/TLS, SMTP, offsiteåterställning och belastningsprov är externa acceptans- och
driftuppgifter. Lokala tester får inte ändra deras status till genomförda.

[MIT-licensen](../LICENSE) och det [publika repot](https://github.com/freecollab-eu/freecollab)
gäller. Endast Kris Ledel ska stå som commitförfattare och committer; inga
coauthor-trailers läggs till. Driftsättning beskrivs i [Railway-guiden](../RAILWAY_DEPLOY.md).
