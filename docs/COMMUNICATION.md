# Samtal, trådar och närvaro

Den autentiserade fullappen använder aktuella konto-, arbetsyte- och kanalrättigheter. Native meddelanden lagras serverkrypterade; [Matrix-klientkryptering](E2EE_IMPLEMENTATION.md) är en separat väg utan serverklartext. Den publika [produktdemon](PRODUCT_DEMO.md) använder samma klient och API med isolerade tillfälliga exempelkonton. Den är en riktig avgränsad chattmiljö, men verifierar inte en native telefonklient eller externa leverantörstjänster.

## Historik och trådar

Huvudsamtalet läser `GET /messages/channels/{channel_id}?roots_only=true`. Filtrering av trådsvar sker före sidgränsen, så ett aktivt samtal med många svar inte gömmer ursprungsmeddelandet. Parametern är valfri och har standardvärdet `false`: historiksökning och befintliga all-message-konsumenter får fortfarande både rötter och svar. `before` är ett faktiskt meddelande-ID och native sidstorlek är 1–100.

Öppna en tråd och använd **Load earlier replies** när äldre svar finns. `GET /threads/messages/{message_id}` stöder `limit`, `before` och returnerar `has_more` utifrån den verkliga queryn. Svar visas kronologiskt; lika tidsstämplar får ett stabilt ID-ankare. Äldre sidor behåller aktuella socketredigeringar/tombstones och användarens läsposition. En äldre trådsökträff laddar sidor till sitt aktuella svar och fokuserar det. Om svaret tagits bort visas det som otillgängligt.

Varje inläsning binds till den inloggning, kanal och tråd som startade den; ett sent svar får inte fylla ett nytt samtal. Huvudutkast är enhetskrypterade enligt [mobilavtalet](MOBILE_OFFLINE.md). Trådutkast hålls separat per tråd i den öppna appens minne och bevaras när äldre svar laddas; de är inte permanent synkroniserade mellan enheter. Ett kontobyte rensar dem.

I native samtal finns **Save in document stack** även på trådens ursprungsmeddelande och varje läsbart svar. Handlingen stänger tråden och öppnar dokumentverktygets förhandsgranskning av just den källan. Ingen referens sparas förrän användaren väljer stapel och bekräftar. Ett osänt trådutkast finns kvar när tråden öppnas igen. Funktionen är inte tillgänglig i Matrix-kanaler och skapar ingen klartextfallback där.

Matrix läser och verifierar historiken på den betrodda enheten. Sidräkning för huvudsamtalet filtrerar trådrelationer innan sidgränsen men behåller avsändarverifierade redigeringar och trådräkning. Trådpagineringen använder den befintliga betrodda Matrix-adaptern; det finns ingen native klartextfallback om krypterad historik eller dess nycklar är otillgängliga.

## Närvaro och egen statustext

Statusväljaren sparar `online`, `away` eller `busy` och en valfri egen text. Klienten väntar på serverns kvittens innan väljaren stängs. Ett fel lämnar texten kvar för nytt försök. Aktuell text läses genom det vanliga synlighetskontrollerade presence-API:t, och sena laddningar får inte ersätta en ny persons status eller det användaren redan skriver.

Socket.IO använder API-adressens origin och standardvägen `/socket.io`; REST-prefix som `/api` blir inte ett Socket.IO-namespace. En statusändring kräver en ansluten socket vars aktuella handshake tillhör samma inloggning. Statustext köas inte över frånkopplingar eller till ett annat kontos nästa anslutning; ett misslyckat försök kräver ett nytt uttryckligt val.

Socket.IO-händelsen `set_status` accepterar högst 50 tecken i `custom_status`, utan kontrolltecken. Utelämnat fält behåller **samma anslutnings** text; `null`, tom text eller endast blanksteg rensar den. Texten är vanlig statusmetadata som tillåtna katalogkollegor kan se, inte krypterat kanalinnehåll. API/broadcast exponerar inte token, sessionshash eller verifierade claims. Återkallade sessioner och personer utanför aktuell katalog kan inte publicera eller läsa närvaron.

Närvaro är tillfällig, per aktiv anslutning, med samma modell lokalt och i den valfria privata Valkey-runtime som använder tidsbegränsade leases. Senast uttryckligt ändrade aktiva anslutning gäller; en periodisk lease-refresh ändrar inte den ordningen. En ny anslutning börjar online utan ärvd statustext. Om den stängs kan en annan fortfarande giltig enhets status åter bli aktuell. Sista frånkoppling, utgången lease eller återkallad auktoritet ger offline utan egen text. Statustext är därför inte en permanent användarprofil, en möteskalenderstatus eller bevis på att någon faktiskt läser appen.

## Färsk auktoritet vid skrivning

Native send/edit/delete, trådsvar och tillägg/borttagning av reaktioner kontrollerar den ursprungliga aktören igen efter kanalens skrivlås. Logout, blockering eller ändrad kontosäkerhetsversion vid låsgränsen ger HTTP401 innan meddelande, trådräknare eller reaktion ändras. Kanalmedlemskap, native/Matrix-läge och befintliga innehållspolicyer kontrolleras fortfarande separat.

## Verifiering och plattformsgräns

[Backendregressioner](../backend/tests/test_communication_revocation.py), [native historik](../backend/tests/test_message_history.py), [närvaro/socket](../backend/tests/test_socket_security.py), [privat Valkey/TLS](../backend/tests/test_valkey_runtime.py), [klientnärvaro](../frontend/tests/presence.test.mjs) och [trådprojektion](../frontend/tests/thread-history.test.mjs) verifierar dessa kontrakt. Den isolerade kommunikationsresan använder fullappen, egna konton/API/SQLite/Vite/Chromium. [Produktdemons browserproof](../frontend/tests/product-demo-browser.py) kör samma gränssnitt och API med tillfälliga exempelbesök i en separat privat testmiljö; den fastställer inte publik drift.

Kundsignerade Android/iOS-appar, Apple/Firebase-leverans och fysisk bakgrundsringning kräver egen acceptans enligt [native push](NATIVE_PUSH.md), [native calling](NATIVE_CALLING.md) och [native klienter](NATIVE_CLIENTS.md). Ett godkänt browser-/protokolltest ersätter inte dessa kontroller.
