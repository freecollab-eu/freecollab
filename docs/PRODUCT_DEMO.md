# Produktdemon: samma app med tillfälliga exempeldata

Demon ska visa **FreeCollabs riktiga produktgränssnitt och API**, inte en separat
illustration av funktionerna. Den särskilda demoingången monterar ordinarie
`Chat` med samma språk-, utseende- och kontoproviders. Dokument, högar, uppgifter,
filer och övriga verktyg använder ordinarie `WorkHub` och produktkomponenter.
En ändring i dessa komponenter ska därför synas även i demon.

## Vad som är samma

- Samtalets layout, kanalval, verktygsmenyer, skrivfält, meddelandeåtgärder,
  trådar, reaktioner, kanaldetaljer och navigation.
- Dokumentredigering med riktiga Yjs-uppdateringar, kommentarer, högar,
  placeringar, ångra och meddelandereferenser till originalet.
- Projekt-/uppgiftsmodeller, filuppladdning/-hämtning och personliga
  utseende-/språkinställningar.
- Arbetsflödets start, godkännande och riktiga bakgrundsjobb som skapar nästa
  uppgift; exempelflödet har inga förfyllda lyckade körningar.
- Ordinarie autentisering, medlemskap, revisioner, validering och API-fel.
  Det finns ingen generell transport som ersätter produktens svar med falska
  lyckade demosvar.

## Samma sammanhängande arbetsvy

Den gemensamma produktvyn håller samtalet och dess skrivfält kvar när man
öppnar delat arbete. Den valbara **Kanalintroduktionen** nås via **Kanalmeny**;
där kan materialkolumnen i en vanlig native-kanal visa en
befintlig bild eller fil, en delad sida med ett kort utdrag och dess dokumenthög,
samt ett konkret ofärdigt steg från kanalens senaste projekt. Dessa är riktiga
produktposter, inte kort med hårdkodad demotext. En tom kanal får inget påhittat
material.

Översikten är läsande: den skapar inga filer, dokument, kopplingar eller
uppgifter. Den rekonstruerar högst en vald sidas verkliga Yjs-innehåll i minnet
och läser uppgifter från högst fyra aktuella projekt. Läsjobben körs ett i taget;
fokus-, synlighets- och uppdateringshändelser köar högst en ny läsning. Detta är
en översikt över valda aktuella poster, inte en komplett inventering av allt
arbete eller ett AI-beslut om vad teamet ska göra härnäst.

Kontot, sessionen, arbetsytan och kanalen kontrolleras både före visning och vid
klick. Sena svar från tidigare sammanhang får inte bli material i den nya
kanalen. Denna native-översikt används inte som klartextfallback för krypterade
kanaler; deras ordinarie dokument-, uppgifts- och filverktyg behåller sina egna
verifierade krypteringsflöden.

Samma layout och produktkomponenter gäller i egen installation. Demons
exempelpersoner och gårdsprojekt fyller de vanliga posterna; de väljer inte ett
annat gränssnitt. Användarens egna färger, typsnitt, täthet och språk förblir
personliga inställningar.

I en bred vy kan materialet flyttas till vänster eller höger via verktygsmenyn;
placeringen gäller bara användarens aktuella session och kanal. På smala skärmar
ligger samtal och material i en kolumn. Dokument-, hög- och uppgiftslänkar öppnar
den riktiga posten i samtalets arbetsregion. **/ Alla funktioner** samlar
dokument, uppgifter, beslut, möten, kanalaktivitet, arbetsflöden och andra
tillåtna verktyg. Skrivfältet prioriterar text, bilagor och sändning;
**Meddelandealternativ** samlar schemaläggning, fokus och hjälp för aktuellt
utkast. Den vanliga samtalsvyn har ingen
verktygsflikrad och arbetsregionen är inte modal. Man kan fortsätta skriva och
skicka meddelanden medan det riktiga dokumentet, uppgiften eller beslutet är öppet.

Ett meddelande kan uttryckligen föras vidare till ett dokument, nästa steg eller
beslut. Den ordinarie resursen sparas först. Dokument- och uppgiftslänkar använder
en beständig koppling med endast resursens och källmeddelandets identifierare.
Beslut har redan ett källmeddelandefält i sin ordinarie revision; länken härleds
direkt från det sparade beslutet. Innehåll och status läses från de vanliga
behörighetskontrollerade API:erna. Ett klick öppnar den
exakta posten. En kompakt utfällbar sammanställning räknar unika dokument,
uppgifter och beslut för diskussionen, även arbete från ännu oöppnade svar.
Roten härleds ur verklig native-källmetadata; ofullständiga laddningar benämns
tillgängligt arbete. Dessa meddelandekopplingar stöds i native-kanaler. Krypterade
kanaler får ingen klartextfallback. Arbetsflöden är fortfarande kanalens vanliga
arbetsflöden med start, godkännande och bakgrundsjobb, inte meddelandebundna
flödesinstanser.

Stängning och byte av arbete, kanal eller arbetsyta kontrollerar osparade ändringar
innan den aktuella vyn ersätts. Pågående dokumentsynkronisering samt sparningar av
dokument, uppgifter och beslut behöver slutföras innan man lämnar dem. Vanliga osparade utkast får
en bekräftelse; avböjer man lämnas redigeraren och utkastet kvar.
Ett misslyckat sparande ger ingen falsk lyckad koppling. Källmeddelandet kan visas
utan att redigeraren stängs. **Escape** i samtalets skrivfält stänger inte arbetet;
i arbetsregionen gäller dess vanliga skydd mot att lämna osparade ändringar.
Återgång bevarar samma monterade skrivfält och samtalsutkast. Utloggning, ny
session och förlorad behörighet rensar det gamla sammanhanget direkt.

Sparade dokument öppnas som läsbara sidor. **Redigera dokument / Klar med
redigering** växlar visning med samma monterade editor, markering och
Yjs-session; själva visningsbytet lagrar inget. Nya dokument börjar i
redigering och behöver fortfarande skapas uttryckligen. Läsvy och delad,
förstorad eller minimerad arbetsvy är oberoende av varandra. Minimerad läsning
återfår fokus på den synliga sidan, utan att ett dolt textfält tar över.

Dokumentets övriga funktioner finns under **Dokumentalternativ** och
**Ordna dokument**: befintlig historik, arkivering, export, högar, flytt,
ångra och relationer använder samma produktåtgärder som tidigare. Den separata
WorkHub-dialog som uttryckliga SDK-/inbäddningsvyer använder är en egen
presentationsform av de ordinarie verktygen.

Kanalen öppnas vid en uttrycklig källa, en scoped ihågkommen läsposition eller
första olästa inkommande rot som fastställs av verkligt läskvitto och ordning.
Känt lästa samtal öppnas vid slutet. Senaste meddelandet ligger längst ned vid
skrivfältet. Okänd eller låst historik visas utan att automatiskt markeras läst.
**Hoppa till senaste** och ett eget skickat meddelande är uttryckliga val som
vinner över en äldre väntande startladdning. En nyare källänk ersätter äldre
köad källnavigation. Meddelanden använder samma lässkala; kanalens större
rubrik finns i den valbara introduktionen. Tangentbordsnavigation och
materialuppdateringar bevarar den valda läspositionen. Se
[de sex vardagsflödena](FLOW_FIRST_UX.md).

Exempeldata läggs i den riktiga datamodellen: arbetsyta, kanaler, medlemmar,
meddelanden, dokument, högar, kommentarer, uppgifter och filer. Exempelpersonerna
är fiktiva; deras tidigare meddelanden ska inte visas som pågående mänsklig
aktivitet. Deras profilbilder använder det ordinarie behörighetskontrollerade
bild-API:et, med initialer som reserv när en bild saknas.

## Vad som skiljer en offentlig exempelmiljö från egen installation

Varje besök får ett tillfälligt exempelkonto och en egen arbetsyta. En riktig
signerad session används mot samma API. Token hålls i minnet på demosidan;
befintliga produktkonton och deras browserlagring ska varken läsas eller skrivas.
Detta ersätter inte den egna installationens vanliga inloggning.

En liten demoram visar att uppgifterna är exempel, när sessionen slutar gälla
och hur man återställer. **Återställ exempel** kräver ett uttryckligt val och
öppnar en ny isolerad exempelarbetsyta. Omladdning öppnar också en ny session;
demon är inte avsedd för beständigt arbete. Tillfällig serverlagring och utgångna
besök har sin egen begränsade rensningslivscykel.

Den publika miljön har gränser för sessioner, skrivningar, uppladdningar och
lagring. Den kan inte ändra installationens drift, kontosäkerhet eller andra
besökares innehåll. Inga hemliga nycklar ska anges i en publik demo.

I egen installation kan användaren lägga till, byta eller ta bort sin
profilbild i kontoinställningarna. Den publika demon låter ingen ladda upp en
egen profilbild; kontopanelen förklarar denna integritetsgräns. Exempelbilderna
är fortfarande riktiga, tillfälliga produktposter och rensas med besöket.

Externa leverantörer, utgående e-post, klientkrypteringens Matrix-tjänst,
federation, push och externa medietjänster är inte konfigurerade i
exempelinstallationen. De riktiga panelerna ska visa sina normala
avstängda/okonfigurerade tillstånd eller en uttrycklig demobegränsning. Demon
ska inte fabricera lyckad SSO, AI-svar, mötesanslutning eller leverantörssynk.
Att visa samma produktgränssnitt betyder inte att en publik besökare får göra
obehöriga externa anrop eller använda riktiga operatörshemligheter.

## Körning och isolering

`DEMO_INSTANCE` är avstängt i den vanliga produkten. Demo-sessionens publika
bootstraproute finns bara i den särskilda demoinstallationen. Den kräver en
egen engångsdatabas, filkatalog och nycklar under en dedikerad `public-demo`-
katalog; den får inte riktas mot en kundinstallation eller operatörens data.
Startupvalidering ska avvisa en sådan felkoppling.

Webbplatsen visar en skärmbild från den riktiga produktvyn. Bildens knapp öppnar
den separata produktmiljön där besökaren kan använda funktionerna. Webbplatsens
dokumentation och marknadsföringssidor är fortsatt skilda från en kunds installation. Railway
är ett möjligt driftval för den publika demonstrationen, inget krav för att
köra FreeCollab. Se [egen dator, LAN och VPN](SELF_HOSTING.md).

## Kod och verklig verifiering

| Del | Implementation |
| --- | --- |
| Samma produktklient | [demo-entry](../frontend/src/demo-entry.tsx), [sessionram](../frontend/src/components/ProductDemoSession.tsx), [Chat](../frontend/src/components/Chat.tsx), [WorkHub](../frontend/src/components/WorkHub.tsx) |
| Verkligt material bredvid samtalet | [materialvy](../frontend/src/components/ChatSharedMaterial.tsx), [avgränsad läsning](../frontend/src/lib/chat-shared-material.ts) |
| Arbete från ett meddelande | [native-kopplingar](../frontend/src/lib/conversation-work-api.ts), [aktuella resursreferenser](../frontend/src/components/ConversationWorkReferences.tsx), [navigationsskydd](../frontend/src/lib/conversation-navigation.ts) |
| Ordinarie profilbilder | [kontoinställning](../frontend/src/account/AccountPhoto.tsx), [porträtt](../frontend/src/components/Avatar.tsx), [bild-API](../backend/app/api/avatars.py) |
| Webbplatsens ingång | [verklig produktbild](../frontend/src/assets/product/courtyard-product.png), [webbplats](../frontend/src/components/Landing.tsx), [handoff-proof](../frontend/tests/public-launch-browser.py) |
| Riktiga exempeldata och session | [bootstrap-API](../backend/app/api/public_demo.py), [sessionservice](../backend/app/services/public_demo.py), [exempeldata](../backend/app/services/public_demo_seed.py), [besöksmodell](../backend/app/models/public_demo.py) |
| Publik demos avgränsning | [HTTP-policy](../backend/app/services/public_demo_boundary.py), [startupinställningar](../backend/app/config.py) |
| Regressionsproof | [riktig browser/API-körning](../frontend/tests/product-demo-browser.py) |
| Kontinuitet i egen installation | [native-samtal, navigationsskydd och verkliga sparanden](../frontend/tests/conversation-flow-browser.py) |

Browserproofen använder egna temporära API-/Vite-processer och en egen
exempeldatabas. Den ska köra verkliga gränssnittsåtgärder och verifiera deras
effekt via ordinarie API: meddelanden, trådar, reaktioner, kanaldetaljer,
Yjs-dokument, kommentarer, högar/ångra/källöppning, uppgifter, godkända
arbetsflöden med verkligt bakgrundsjobb, filer och
utseende. Den kontrollerar också besökarisolering, återställning, språk/mobil,
lagringsskydd och frånvaro av externa anrop.

Med backendens Python-beroenden, klientens npm-beroenden samt Playwright och
Chromium installerade kan proofen köras från repots rot:

```sh
BACKEND_PYTHON=backend/.venv/bin/python python3 frontend/tests/product-demo-browser.py
```

`BACKEND_PYTHON` ska peka på en befintlig interpreter med backendberoendena.
`CHROMIUM_PATH` och `PRODUCT_DEMO_PROOF_REPORT` kan ange annan browser respektive
rapportplats. Processerna, databasen, nycklarna och filerna skapas temporärt och
tas bort efter körningen; rapport och skärmbilder behålls separat.

Den integrerade produktdemon kördes **2026-10-23 med 33/33 grupper godkända**
mot riktiga temporära API-, klient- och workerprocesser. Utöver befintliga
meddelande-, dokument-, hög-, uppgifts- och arbetsflöden verifierades arbete
direkt från meddelanden: sparade dokument och uppgifter återöppnades genom
sina verkliga resurskopplingar, och beslut behöll sitt ursprungsmeddelande.
Ett riktigt meddelande kunde skickas medan samma Yjs-redigerare låg kvar.
Osparade uppgiftsutkast överlevde avböjd navigation. Svenska vid 320 px
verifierade synlig dokumentredigering, sparad text efter återöppning och
bevarat samtalsutkast. **558 käll-, konfigurations- och proof-filer** matchade
arbetskopian vid avslut. Inga externa anrop, sidfel, åtkomster till bevarade
kontonycklar, IndexedDB-operationer eller service worker-registreringar
observerades. Ägda processer, tillfälliga data och nycklar rensades.

Den privata automationen väntade minst sex sekunder mellan slutförda grupper
och längre när den uppmätta anropstakten krävde det. Produktens API och
begränsningspolicy användes oförändrade; inga HTTP 429-svar förekom.

Den integrerade arbetsregionen verifierades **2026-10-23 med 15/15 grupper** i
den vanliga installationens separata browserproof. Den körde riktiga
dokument-, uppgifts- och beslutssparningar, blockerade byte medan en verklig
Yjs-skrivning väntade och behöll osparade utkast vid avböjd navigation mellan
arbete, kanaler och arbetsytor. Ett riktigt meddelande kunde skickas medan
dokumentredigeraren låg kvar. Beslutets sparknapp kunde nås med vanlig
pekarnavigation i den rullningsbara arbetsregionen. Kontobilder, sessionsbyte,
återgång och svenska vid 320 px ingick. **555 käll-, konfigurations- och
proof-filer** matchade arbetskopian när körningen avslutades.

Föregående produktversion, före den integrerade arbetsregionen, kördes
**2026-10-23: 27/27 browsergrupper godkända** mot riktiga temporära API-,
klient- och workerprocesser. Körningen behöll de tidigare native-flödena och
verifierade dessutom riktiga gårds-/profilbilder, exakta materiallänkar,
tangentbordsåtergång, fler än tolv meddelanden och en flerradig composer med
material på båda sidor. Vid 320 px kunde ett riktigt dokument redigeras och
öppnas igen med sparad text, bevarat samtalsutkast och samma synliga källkontroll.
**550 käll-, konfigurations- och proof-filer** hade samma SHA-256 i den privata
kopian som i arbetskopian vid avslut. Browserkörningen observerade inga externa
anrop, sidfel, läsningar/skrivningar av bevarade kontonycklar, IndexedDB-operationer
eller service worker-registreringar. Alla ägda testprocesser och tillfälliga data
rensades.

Den föregående paritetsversionens **16/16 grupper och
533 runtimekällor** är historiskt underlag, separat redovisat i
[valideringshistoriken](VALIDATION.md).

Den privata proofen fastställer inte publik Railway-deployment, TLS, kapacitet,
fysisk mobilfunktion eller acceptans mot en verklig leverantör. En senare
ändring i runtimekoden behöver ny verifiering; en planerad kontroll räknas inte
som godkänd innan den har körts.
