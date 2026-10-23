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
öppnar delat arbete. Materialkolumnen i en vanlig native-kanal kan visa en
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
den riktiga posten i de ordinarie verktygen. När verktygen stängs återgår man till
samtalets läsposition och kontroll, med samma monterade skrivfält och utkast.

En komplett kanal utan tidigare läsposition öppnas vid samtalets början.
**Hoppa till senaste** leder till nya meddelanden; direktchattar, sidindelad
historik och exakta källänkar behåller sina ordinarie läs- och återgångsflöden.
Tangentbordsnavigation och uppdatering av material ska bevara användarens valda
läsposition.

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
| Ordinarie profilbilder | [kontoinställning](../frontend/src/account/AccountPhoto.tsx), [porträtt](../frontend/src/components/Avatar.tsx), [bild-API](../backend/app/api/avatars.py) |
| Webbplatsens ingång | [verklig produktbild](../frontend/src/assets/product/courtyard-product.png), [webbplats](../frontend/src/components/Landing.tsx), [handoff-proof](../frontend/tests/public-launch-browser.py) |
| Riktiga exempeldata och session | [bootstrap-API](../backend/app/api/public_demo.py), [sessionservice](../backend/app/services/public_demo.py), [exempeldata](../backend/app/services/public_demo_seed.py), [besöksmodell](../backend/app/models/public_demo.py) |
| Publik demos avgränsning | [HTTP-policy](../backend/app/services/public_demo_boundary.py), [startupinställningar](../backend/app/config.py) |
| Regressionsproof | [riktig browser/API-körning](../frontend/tests/product-demo-browser.py) |

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

Kört **2026-10-23: 27/27 browsergrupper godkända** mot riktiga temporära API-,
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

Automationen väntade sex sekunder mellan slutförda grupper för att följa
exempelmiljöns ordinarie anropsgränser. Produktens API och begränsningspolicy
användes oförändrade. Den föregående paritetsversionens **16/16 grupper och
533 runtimekällor** är historiskt underlag, separat redovisat i
[valideringshistoriken](VALIDATION.md).

Den privata proofen fastställer inte publik Railway-deployment, TLS, kapacitet,
fysisk mobilfunktion eller acceptans mot en verklig leverantör. En senare
ändring i runtimekoden behöver ny verifiering; en planerad kontroll räknas inte
som godkänd innan den har körts.
