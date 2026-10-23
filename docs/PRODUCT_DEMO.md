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

Exempeldata läggs i den riktiga datamodellen: arbetsyta, kanaler, medlemmar,
meddelanden, dokument, högar, kommentarer, uppgifter och filer. Exempelpersonerna
är fiktiva; deras tidigare meddelanden ska inte visas som pågående mänsklig
aktivitet.

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

Webbplatsen kan öppna denna separata produktmiljö. Webbplatsens dokumentation
och marknadsföringssidor är fortsatt skilda från en kunds installation. Railway
är ett möjligt driftval för den publika demonstrationen, inget krav för att
köra FreeCollab. Se [egen dator, LAN och VPN](SELF_HOSTING.md).

## Kod och verklig verifiering

| Del | Implementation |
| --- | --- |
| Samma produktklient | [demo-entry](../frontend/src/demo-entry.tsx), [sessionram](../frontend/src/components/ProductDemoSession.tsx), [Chat](../frontend/src/components/Chat.tsx), [WorkHub](../frontend/src/components/WorkHub.tsx) |
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

Kört **2026-10-23: 16/16 browsergrupper godkända** mot riktiga temporära API-,
klient- och workerprocesser. Det omfattade även Microsoft-/Google-guider med
faktiskt avstängd konfigurering, godkännande som skapade en uppgift via den
ordinarie workern, isolerade besök/återställning och svenskt gränssnitt vid
320 px. **533 runtimekällor** hade samma SHA-256 i den privata källkopian som
i arbetskopian vid avslut. Inga externa anrop, sidfel, läsningar/skrivningar av
bevarade kontonycklar, IndexedDB-operationer eller service worker-registreringar
observerades. Alla ägda testprocesser och tillfälliga data rensades.

Den privata proofen fastställer inte publik Railway-deployment, TLS, kapacitet,
fysisk mobilfunktion eller acceptans mot en verklig leverantör. En senare
ändring i runtimekoden behöver ny verifiering; en planerad kontroll räknas inte
som godkänd innan den har körts.
