# Från samtal till gemensamt arbete

De här funktionerna finns i den vanliga klienten och används också av
[produktdemon](PRODUCT_DEMO.md), med dess isolerade exempeldata. De kräver ingen
AI-leverantör. Förslag är lokala och ändringar sker först när någon väljer dem.

## Gör en nästa handling av det som redan sagts

Markera en mening i ett kanalmeddelande eller trådsvar och välj
**Skapa ett nästa steg**. Samma åtgärd finns bland
meddelandets verktyg och vid trådens inlägg.
Ett vanligt uppgiftsutkast öppnas bredvid samtalet. Granska titel, beskrivning,
ansvarig och datum, ändra det som behövs och välj **Spara**.

Förslaget behåller den valda texten och dess valda `@`-referenser. Ansvarig kan
föreslås utifrån en vald personreferens, ett entydigt aktuellt medlemsnamn eller
ett entydigt förnamn i början av en tydlig uppmaning. Engelska och svenska
datumuttryck tolkas i användarens tidszon. Otydliga personer och motstridiga datum
lämnas tomma. Medlemskapet kontrolleras igen när formuläret öppnas; ett senare
manuellt val eller en tömd ansvarig ersätts inte av förslaget.

Att markera text eller öppna utkastet skapar ingen uppgift. Borttagen, låst,
ändrad eller blandad text från flera meddelanden får inte bli ett gammalt
förslag. Ett sparat native-arbete länkas till sitt ursprung genom den befintliga
meddelandelänken. Krypterade uppgifter behåller utdrag och referenser i sitt
krypterade innehåll; den beständiga native-länken mellan meddelande och arbete
finns ännu inte för krypterade samtal.

## Förstora dokumentet och hitta tillbaka

Ett dokument som öppnas från ett meddelande behåller en kompakt samtalsremsa när
det förstoras. Den visar aktuell kanal, tillgänglig avsändare och ett utdrag ur
det verkliga ursprungsmeddelandet. **Tillbaka till ursprungsmeddelandet** visar
samtalet igen och öppnar rätt tråd när källan är ett svar.

Återgången stänger inte dokumentet. Editorn, Yjs-sessionen och chattutkastet
ligger kvar. En otillgänglig källa visas inte som om den fortfarande gick att
öppna. Nyare navigation, kanalbyte och kontobyte gör äldre återgångar ogiltiga.

## Hitta rätt med `/` och `@`

Exakta träffar kommer först. Därefter prioriteras val som passar fältet och
arbetet, följt av nyligen använda val i samma session och kanal. Till exempel
prioriteras personer i ett ansvarigfält och dokument i dokumentarbete.
Övriga tillåtna funktioner går fortfarande att söka fram.

Ordningen ändrar inte kommandonas argument, rollgränser eller valda
referens-ID:n. Historiken innehåller endast kommandons och referensers identiteter
i minnet, är begränsad i storlek och rensas vid sessionsbyte. Ingen meddelandetext
eller sökfras sparas som användningshistorik. Se
[hela samarbetskontraktet](UNIVERSAL_COLLABORATION.md) och
[kommandolistan](SLASH_COMMANDS.md).

## Samla dokument med ett lugnt förslag

När du börjar ordna ett dokument kan ett enda förslag visa vilken befintlig hög
det hör ihop med. Förklaringen anger gemensamma ord från aktuella synliga titlar
och olösta kommentarer. Dokumentinnehåll hämtas inte för detta förslag.

Välj **Flytta till …** eller **Avvisa förslag**. Två lika starka alternativ ger
inget förslag. Ett accepterat förslag kontrolleras mot färsk organisation innan
flytten sparas. Det avvisade förslaget återkommer inte vid varje uppdatering av
samma vy. Se [dokumentorganisation](DOCUMENT_ORGANIZATION.md).

## Kom tillbaka till det som hänt

**Sedan du läste senast** visar aktuella beslut, uppdaterade dokument samt
uttryckliga omnämnanden, svar på dina meddelanden och fortfarande öppna uppgifter
som tilldelats dig. Översikten använder lästiden som fångades när du kom in i
kanalen, även om du sedan läser nyare meddelanden.

En fullständig native-översikt utan förändringar tar ingen plats. På låga
mobilskärmar går introduktionen och en öppnad översikt att skrolla inom sina
ytor, så att meddelanden och skrivfält fortfarande går att använda.

Varje rad öppnar den verkliga källan genom samma behörighetskontroller och
utkastskydd som övrig navigation. Att hämta översikten markerar inget som läst.
Vanlig läskvittering fortsätter när meddelanden faktiskt visas enligt klientens
befintliga regler.

Väljer du **Gå till senaste** följer vyn även när kopplat arbete laddas in
senare. Läser du längre upp behåller chatten din plats. En hopfälld arbetsyta
tar inte en dold kolumn från samtalet.

Detta är en begränsad översikt av tillgängliga poster, inte en genererad
tolkning av hela samtalet. Den anger när historik saknas eller en gräns nåtts.
Krypterade samtal använder verifierad, klientdekrypterad historik och visar
uttryckligen att äldre eller låsta poster kan saknas. Saknad tidigare lästid
ger ingen påhittad återkomsthistorik. Översikten anropar ingen modell och
publicerar inga väntande krypterade ändringar.

## Ångra organisation utan att skriva över andra

Efter en flytt, ett namnbyte på en hög eller skapandet av en tom hög visas en
synlig **Ångra**-åtgärd även när organisationsverktygen är hopfällda. Den senaste
åtgärdens kvitto finns kvar medan panelen är öppen.

Ångra kontrollerar exakt version och högens generation. En senare ändring från
en kollega ger konflikt i stället för att skrivas över. Att ångra en flytt
ändrar bara placeringen; senare dokumenttext och kommentarer behålls. En ny hög
kan bara tas bort genom Ångra medan den fortfarande är tom. Detta gäller
organisationen, inte allmän raderingsåterställning eller dokumentets texthistorik.

## Verifiering

Fokuserade enhetstester finns för uppgiftsutkast, textmarkering, samtalsremsa,
kontextordning, återkomstöversikt och organisationskvitton. Browserproven
`natural-collaboration-browser.py`, `suggestion-ranking-browser.py`,
`channel-return-browser.py`, `channel-return-demo-browser.py` och
`document-organization-browser.py` använder
ordinarie klient med tillfälliga riktiga API-konton och data. De senaste körda
resultaten och deras gränser anges i [valideringsloggen](VALIDATION.md).
