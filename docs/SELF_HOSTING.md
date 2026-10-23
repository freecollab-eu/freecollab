# Kör FreeCollab på egen dator eller i eget nätverk

FreeCollab är en **självhostad samarbetsprodukt**, med samma installationsidé
som exempelvis Zulip: ni driver en egen installation och användarna ansluter
till den. Jämförelsen gäller driftmodellen, inte identisk arkitektur eller
funktionsparitet. Det krävs inget konto, abonnemang eller kontrollplan hos en
central FreeCollab-tjänst.

Konton och inloggning hör till er egen installation. Ni kan använda lokala
konton eller konfigurera organisationens egen identitetsleverantör. När annan
dokumentation säger ”autentiserad produkt” avses denna arbetsyta, till skillnad
från webbplatsens kontofria demo.

## En dator

På en Linuxvärd med Docker Compose v2 och Python 3.11+, från repots rot:

```sh
python scripts/pilot-init.py
docker compose --env-file pilot.env --project-name freecollab up --build -d --wait
```

Öppna `http://127.0.0.1:8080`. Startprofilen lyssnar bara på den egna datorn;
databas och API har inga publicerade portar. `pilot` är det befintliga namnet
på installationsprofilen, inget krav på en pilotgrupp eller molntjänst.
Behåll den privata konfigurationen och nycklarna. Se
[hela driftguiden](OPERATIONS.md) för konton, administratör, beständig lagring,
uppgradering och återställning.

## Team på LAN eller VPN

Kör samma installation på en egen server och låt en omvänd proxy nå dess
loopbackbundna frontend. Ge den ett stabilt internt DNS-namn och **HTTPS med
ett certifikat som klienterna litar på**. Den behöver inte vara åtkomlig från
internet. Följ driftguidens produktionsinställningar för `PUBLIC_APP_URL`,
exakta CORS-/värdursprung och betrodda proxyadresser. Proxyn måste vidarebefordra
både `/api/` och `/socket.io/` inklusive WebSocket-uppgradering.

Vanlig HTTP på en LAN-IP är inte likvärdig med localhost: bland annat
Web Crypto, PWA, mikrofon och kamera har krav på säker browserkontext. Publicera
inte databasen eller utvecklingsservern som lösning på nätverksåtkomst. Ett
internt CA-certifikat går bra när det är installerat och betrott på klienterna.

## Egna tjänster och valbara externa anslutningar

| Funktion | Driftmodell |
| --- | --- |
| Kanaler, meddelanden, filer, dokument, uppgifter och workflows | Egen API/databas/lagring och egna workerprocesser. Ingen Microsoft-, Google- eller AI-nyckel behövs för grundprodukten. |
| Lokal inloggning | Konton i er installation. E-postverifiering, återställningsleverans och inbjudningsmail behöver fungerande egen SMTP när de används. |
| Klientkrypterade kanaler | Egen konfigurerad Matrix-tjänst och klienternas enheter/nycklar; ingen central FreeCollab-homeserver krävs. Federation är ett separat val. |
| Webbmöten | Egen signalering; LAN/NAT avgör behov av egen STUN/TURN. LiveKit är en valbar separat tjänst för SFU/bakgrundssamtal. |
| Microsoft 365, Google och andra leverantörer | Valbara adaptrar. Kunden registrerar/auktoriserar sin egen app. Dessa funktioner behöver nätåtkomst till vald leverantör. |
| AI | Valbart och avstängt tills konfigurerat/samtyckt. Dagens adapter kräver kompatibel publik HTTPS-endpoint; privat LAN-Ollama/vLLM är ännu inte en implementerad adapter. |
| Push till browser/iOS/Android | Valbart; leverans använder respektive plattforms pushinfrastruktur och kundens konfiguration. Lokala realtidsnotiser är ett separat flöde. |
| Produktwebbplats/Railway-demo | Fristående visningsmiljö. Egen installation använder inte den som backend eller inloggningstjänst. Railway är ett valbart hostingalternativ. |

Helt isolerad airgapinstallation är inte verifierad. Första bygge och installation
behöver paket/containerbilder, och valda externa funktioner behöver sin egen
utgående nätåtkomst. [Infrastrukturval](INFRASTRUCTURE.md),
[kanalkryptering](E2EE_IMPLEMENTATION.md) och
[aktuell funktionsstatus](FEATURE_AUDIT.md) beskriver gränserna.
