# FreeCollab: funktioner, implementation och kvarvarande luckor

**Driftmodellen är egen installation**, på dator eller server i lokalt nätverk,
VPN eller valfri hosting. Inloggningen hör till den egna installationen; ingen
central FreeCollab-molntjänst krävs. [Lokal drift och nätverk](SELF_HOSTING.md)
förklarar grundprodukten, intern HTTPS och valbara externa tjänster.

Granskad **2026-10-23**. Detta är den aktuella funktionsförteckningen för
granskningsgrenen `design/open-studio`, efter `c2762404`. Ändringarna är ännu
inte en publicerad produktversion eller sammanslagna till `main`. Äldre planer
och versionsanteckningar beskriver sina respektive tidpunkter; denna lista och
de länkade implementationsavtalen har företräde för dagens status.

**Alla tidigare önskemål är inte färdiglevererade.** Granskningen har rättat
verkliga luckor och identifierat andra som återstår. En visningsmiljö, en adapter
eller en framtidsplan räknas inte som en fungerande produktfunktion.

- **Implementerat:** produktkod och ett faktiskt UI/API-/lagringsflöde finns
  inom angiven gräns, med länkade tester.
- **Kundkonfiguration/extern acceptans:** kod finns, men kundens registrering,
  samtycke, infrastruktur eller fysisk enhet behövs för slutlig verifiering.
- **Demo:** samma produktkomponenter och API med tidsbegränsade exempelkonton i en separat installation. Inga kundkonton eller externa modeller ansluts. Se [produktdemon](PRODUCT_DEMO.md).
- **Saknas:** dokumenterat önskemål eller utbyggnad utan komplett produktflöde.

## Spårbarhet till beslut och byggda produktdelar

| Beslut / område | Aktuell produkt och underlag | Viktig gräns |
| --- | --- | --- |
| Ursprungliga 1–8: konto, historik/sök, omnämnanden/notiser, arbetsytor/gäster, filer, sparat/beslut, DM, kryptering | [Ursprungskraven](PRODUCT_ROADMAP.md); [identitet/säkerhet](audit/IDENTITY_SECURITY.md), [kommunikation](audit/COMMUNICATION_CLIENTS.md), [innehåll](audit/WORK_CONTENT.md). | Matrix är en separat klientkrypterad väg. Vanliga kanaler är serverläsbara; alla verktyg har inte Matrix-paritet. |
| De sex samarbetsdelarna: projekt/uppgifter, möten, mobil/offline/push, delade dokument, workflows, datakontroller | [0.8-historik](PRODUCT_PLAN.md), [innehåll](audit/WORK_CONTENT.md), [kommunikation/klienter](audit/COMMUNICATION_CLIENTS.md), [automation](audit/AI_WORKFLOWS.md). | WebRTC och valbar SFU, begränsade workflowsteg och separata plattforms-/driftkrav. |
| Weavy-spåren: språk, filer/Office, feed, SDK, molnfiler, AI, externa möten | [Historik 0.9](PRODUCT_090.md), [aktuell jämförelse](WEAVY_COMPARISON.md), [integrationer](audit/INTEGRATIONS.md), [SDK](audit/SDK_OPERATIONS.md). | Implementerade avgränsade flöden bevisar inte full Weavy-paritet, 100+ previewformat eller dess kompletta agentplattform. |
| Kundens SSO och anslutningar | [32 granskade integrationspunkter](audit/INTEGRATIONS.md), [identitet](audit/IDENTITY_SECURITY.md), [anslutningsguide](CONNECTION_SETUP.md). | Microsoft 365 **och** Google använder kundens egna appar. SSO och innehållsgrants är skilda. Ingen gemensam central kundnyckel behövs. |
| Full inbäddning i andra produkter | [SDK-/driftgranskning](audit/SDK_OPERATIONS.md), [SDK](COMPONENT_SDK.md), [kundidentitet](EMBED_IDENTITY.md). | Elva native-moduler/Web Components/React, separat betrodd ram för Matrix. Paketet kan byggas; npm-publicering är separat. |
| Slack-inspirerat 0.11–0.13: gruppchatt, schemaläggning, påminnelser, klienter, delade kanaler, appar, sök, klipp, styrning, egen lagring och delad koordinering | [0.11](PRODUCT_011.md), [0.12](PRODUCT_012.md), [0.13](PRODUCT_013.md), [69 kommunikationspunkter](audit/COMMUNICATION_CLIENTS.md), [AI/appar/workflows](audit/AI_WORKFLOWS.md), [identitet/styrning](audit/IDENTITY_SECURITY.md). | Detta är ingen allmän Slack-klon eller kapacitetsgaranti. Saknade bredare funktioner listas nedan. |
| Skandinavisk form, personlig typografi/färg, språk, mobil och mer naturligt flöde | [UX/språkgranskning](audit/UX_LANGUAGES.md), [designsystem](DESIGN_SYSTEM.md), [utseende](APPEARANCE.md), [språk](LANGUAGES.md). | Svenska/engelska. Faktisk användbarhet med olika användargrupper behöver fortsatt undersökas. |
| Enklare kanalhantering | [Kanalprofil med namn/beskrivning](CHANNELS.md), [verklig UI/API-kod](../frontend/src/components/ChannelMetadataPanel.tsx), [backendtester](../backend/tests/test_channel_metadata.py). | Aktuell administratör, CAS och krypteringsläge kontrolleras. Nativeprofil förseglas; Matrix använder verifierad opak eventreferens. |
| Dokumenthögar, flytta/ångra, kopplingar, samtalets ursprung | [Dokumentorganisation](DOCUMENT_ORGANIZATION.md), [innehållsgranskning](audit/WORK_CONTENT.md), [produktpanel](../frontend/src/components/SharedDocumentOrganization.tsx), [API-tester](../backend/tests/test_document_organization.py). | Verkliga native-sidor, inte bara demon. Meddelanden sparas som referenser; texten kopieras inte. Matrix-högar och generell filtaxonomi saknas. |
| Föreslå och koppla kommentarer/dokument/högar | [Aktuell organisation](DOCUMENT_ORGANIZATION.md). Kommentarer hör automatiskt till hög genom dokumentets aktuella placering. Förklarade ordförslag ger granskade dokumentkopplingar. | Ingen autonom semantisk LLM-parning eller dold analys. Explicit Spara krävs för en ny koppling. |
| AI integrerad i arbetet, leverantörsval, varningar, Grok | [66 AI/app/workflow/orkestreringspunkter](audit/AI_WORKFLOWS.md), [Copilot](COPILOT.md). Inlinehjälp för meddelanden/trådar/utkast, dokument/högar och uppgifter. | Kundägd kompatibel HTTPS-provider; OpenAI-/xAI-preset eller egen kompatibel tjänst kräver egen nyckel/modell. Inlinehjälp finns ännu inte i varje produktpanel. |
| Inbyggd orkestrering | [Nuvarande motorer och uttrycklig plan](AI_ORCHESTRATION.md), [granskning](audit/AI_WORKFLOWS.md). Källgranskning, fasta verktyg, manuella godkännanden, app-outbox och framtida privata recaps finns. | En generell varaktig AI-run/step-motor, modellrouting och budgetredovisning är ännu inte byggda. De ska inte beskrivas som levererade. |
| Open source, ren historik, drift och säljbar demo | [MIT](../LICENSE), [repo](https://github.com/freecollab-eu/freecollab), [publik webbplats](../deploy/website/README.md), [Railway](../RAILWAY_DEPLOY.md), [drift](audit/SDK_OPERATIONS.md). | Endast Kris Ledel som commitförfattare/-committer. Granskningsgren och produktdemo ersätter inte kundens egen installation. Demon är nu samma app/API med separata exempelkonton. |

## Luckor som åtgärdades i denna granskning

- `/` i skrivfältet visar en sökbar lista över tillgängliga produktfunktioner,
  med svenska/engelska sökord, korta beskrivningar och tangentbordsval. Knappen
  **/ Alla funktioner** behåller ett pågående meddelande. Roll, krypteringsläge,
  verifierad anslutning och inbäddning styr vilka ingångar som visas. Valet
  öppnar produktens vanliga funktion; inget meddelande eller externt kommando
  skickas automatiskt. Se [funktionslistans kontrakt](SLASH_COMMANDS.md).
- Dokument, nästa steg och beslut öppnas i samtalets egen arbetsyta, med
  meddelanden och skrivfält kvar. Sparat arbete kan öppnas från sitt
  ursprungsmeddelande. Native-kopplingar lagrar endast behörighetskontrollerade
  resurs-ID:n; verkliga dokument, uppgifter och beslut behåller sina ordinarie
  API:er. Krypterade samtal får ingen okrypterad reservkoppling. Historik,
  arkivering och organisation nås genom kontextuella val i samma arbetsyta.
  Se [samtalsflödet och dess gränser](PRODUCT_DEMO.md).
- Kanalnamn/beskrivning har ett riktigt versionsskyddat redigeringsflöde, även
  för verifierad Matrix-metadata. Oregistrerade konkurrerande event får inte
  ersätta den sparade auktoritativa profilen.
- Native-dokumenthögar, separata placements, säkert Ångra och dokumentpar har
  lagring och export/radering. Resursgeneration hindrar gamla operationer från
  att träffa en raderad och återskapad hög.
- Verkliga Chat-meddelanden kan efter aktuell källgranskning sparas i en hög;
  källan öppnas i sitt ursprungliga samtal/tråd. Högkommentarer härleds ur
  sidornas placering. Stack-AI använder uttryckligt valda aktuella sidor.
- Statustext går hela vägen genom nuvarande aktiva enhetssessioner och är
  nåbar från kontoverktygen. Frånkopplade sockets köar inte status till ett nytt konto.
- Trådar kan laddas förbi 50 svar. Huvudhistoriken räknar rötter så att många
  svar inte tränger undan samtalen; SDK-sökning behåller full historik.
- Behörighet kontrolleras på nytt efter väntan på kanallås, även vid meddelande-,
  tråd- och reaktionsskrivning.
- App-AI binder källtext och providerversion till förhandsgranskningen. Recaps
  börjar vid nytt uttryckligt samtycke och backfyller inte äldre samtal.
- AI-källgränsen räknar den verkliga 24 000-teckenskontexten före sändning.
  Inget tyst bortklippande. Den gamla anonyma server-Groq-demon är borttagen.
- Den godkända rumsvyn används i både produkt och demo. Verkliga filer,
  dokument/högar och uppgifter öppnar ordinarie verktyg och behåller samtalets
  utkast och läsposition. Webbplatsen visar en riktig skärmbild från produkten;
  dess knapp öppnar samma app med isolerade exempeldata. Se [produktdemon](PRODUCT_DEMO.md).
- Profilbilder kommer från användarnas egna kontouppladdningar via ett
  behörighetskontrollerat bild-API, med initialer när bild saknas. Byte,
  borttagning och kontoradering hanterar även gamla lagrade bildfiler. Se
  [utseende och profilbilder](APPEARANCE.md).
- Driftpanelen visar samtliga femton workergrupper med verkliga tillstånd,
  inklusive rensning av borttagna profilbilder.
  Lösenordsformulär använder backendens gränser även för Unicode/UTF-8.
- Produktguiden i appen länkar dagens avtal; gamla påståenden om saknat SDK
  och experimentella exempel som produktfunktioner har rättats.

## Kvarvarande produktarbete

Detta är kodluckor, skilda från kundens aktivering av befintliga adaptrar:

1. **Generell AI-orkestrering:** varaktiga runs/steps, cancel/resume, begränsad
   flerstegsplanerare, flera samtidiga modellprofiler/routing och faktisk
   token-/kostnadsbudget. Nuvarande motorer har separata domänkontrakt.
2. **Bredare kontextuell AI:** varje feed-/besluts-/filpanel, autonom semantisk
   gruppering, workspace-RAG/webbsökning/fria HTTP-verktyg. Personliga nycklar och
   native Anthropic/Azure/Gemini/private Ollama-adaptrar är också ej byggda;
   de var inte ett bekräftat val av leverantörsprotokoll.
3. **Organisation för allt innehåll:** Matrix-högar, generella högar för binära
   och externa filer samt textspannsankrade kommentarer. Säker platt
   dokumentredigering/kommentarer finns redan. En separat privat native-kalender
   är också en lucka; Min arbetsdag sammanställer nu tillåtet kanalinnehåll.
4. **Bredare Slack-beteenden:** direkt exekvering av godtyckliga installerade
   appkommandon med argument i skrivfältet, löpande
   Slack-livebridge, persistent semantiskt sökindex, automatisk mötesinspelning
   och kontinuerliga liveundertexter. Dagens appar/import/sök/klipp är snävare.
5. **Klientdistribution och federation:** automatisk desktopuppdatering,
   verifierade mobil-universallänkar och generell federation av hela produkten.
   Delade Matrix-kanaler och avgränsat federerat arbete är egna stödda flöden.

Riktig kund-OAuth/SSO, Office-skrivbordsprogram, fysisk mobilringning,
appbutikssignering, DNS/TLS, SMTP, offsiteåterställning och belastning är
separata acceptans-/driftuppgifter. Lokala protokolltester ska inte byta deras
status till genomförda.

## Samma produkt i demon

Den tidigare separata `Demo*`-implementationen och dess lokala AI-mallar är
borttagna. Den publika länken öppnar [samma Chat/WorkHub](PRODUCT_DEMO.md) mot
vanliga produkt-API:er, med en egen temporär exempelarbetsyta per besök.
Externa integrationer visar produktens verkliga avstängda/okonfigurerade läge.
Demon bevisar därför faktiska native-flöden inom sin driftkonfiguration, inte
kundens leverantörsgrants, fysisk push eller aktiverad Matrix. Normalinstallationer
har ingen anonym `/demo/session`-rutt.

## Verifiering och underlag

Varje domängranskning länkar produktens ingång, UI, API, modell, tester och
gräns. [SDK/drift](audit/SDK_OPERATIONS.md) kompletterar de sex granskningarna.
[Valideringsloggen](VALIDATION.md) skiljer slutkällans tester från baslinjen
och tidigare körningar. Browserproofs använder privata syntetiska konton och
lokala HTTPS-providers; inga kunduppgifter eller riktiga externa meddelanden.

En godkänd testsvit är inte en oberoende säkerhetsrevision eller ett löfte om
full Slack-/Weavy-paritet. Funktionens angivna gräns gäller även när dess test
är grönt.
