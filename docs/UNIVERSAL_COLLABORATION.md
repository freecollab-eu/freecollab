# Samarbete där man skriver

`/` öppnar produktens vanliga verktyg från det pågående arbetet. `@` väljer en
verklig person, kanalgrupp, sida, uppgift, fil eller det aktuella samtalet.
Dokument, uppgifter och diskussioner använder samma kontroller som chatten.

## Flera arbeten samtidigt

Öppna ett dokument, skriv `/task Granska förslaget` på en egen rad och välj
kommandot. En uppgiftsyta öppnas bredvid dokumentet. Dokumentets editor,
Yjs-session och övriga text finns kvar. Uppgiften är ett utkast tills den
sparas med det vanliga formuläret. Chatten går fortfarande att använda.

Varje arbetsyta kan förstoras, återställas eller minimeras för sig. Den
minimerade ytan finns kvar med sitt utkast; dess fortsättknapp återtar arbetet.
Att öppna en ny dokumentlista får inte ersätta ett redan öppet dokument.
En länk till exakt samma dokument eller uppgift kan återanvända dess öppna yta.
När en yta stängs kontrolleras dess egna osparade ändringar och pågående
skrivningar. Ett verkligt kanal- eller arbetsytebyte kontrollerar alla ytor.

Samma flöde gäller i filförhandsvisning och kalenderdialoger. När ett kommando
öppnar annat arbete läggs ursprungsdialogen åt sidan med samma fält och utkast
kvar. **Fortsätt** återtar den. En uppgiftsyta som öppnats från en filkommentar
kan stängas tillbaka till exakt det ursprungliga kommentarsfältet. Även
nästlade dialoger behåller sin ordning; en filkommentar får inte försvinna för
att medlemslistan öppnas. Dialogernas fokusgränser släpps medan de ligger åt
sidan, så att det nya arbetet går att använda med både mus och tangentbord.

Ytorna tillhör aktuell aktör, session, arbetsyta, kanal och krypteringsläge.
De flyttas inte till ett annat konto. Inloggnings- eller behörighetsändringar
gör gamla kommandon och väntande återgångar ogiltiga. En senare avsiktlig
handling får företräde framför en äldre begäran om fokus.

## Kommandon och vanlig text

I ett dokument eller ett annat längre arbetsfält tolkas kommandot på den rad
där markören står. Exempel:

```text
Detta stycke finns kvar.
/task "Kontrollera måtten" Jämför med ritningen.
Detta stycke finns också kvar.
```

Efter ett godkänt val försvinner bara kommandots textspann. Övrig text och dess
referenser behålls. En ändring från en medförfattare medan verktyget öppnas får
inte skrivas över. Avvisade val och Escape behåller den bokstavliga texten.
Valda referenser inuti kommandot följer till rätt titel och innehåll i det nya
utkastet. Överföringen använder de valda textpositionerna, även när två mål har
samma namn; den söker inte efter namn för att gissa ett ID. En osäker
överföring avvisas innan ursprungstexten tas bort.
`//`, URL:er och kodblock behandlas som vanlig text. Den separata
funktionsknappen öppnar listan utan att konsumera text.

En aktiv meny tar Enter innan formuläret får publicera. Piltangenter väljer;
Escape stänger menyn. Touch och tangentbord använder samma funktionslista.
Menyn ligger inom den aktuella dialogens fokusgräns och anpassas till den
synliga skärmytan. Kommandon ger inte samtycke till AI eller anslutna tjänster.
`/schedule` från ett arbetsfält öppnar schemaläggningsverktyget utan att göra
dokumentets text till ett kanalmeddelande. Från en tråd gäller fortfarande
påminnelsen om trådens ursprungsmeddelande.

Hela listan, argumenten och rollgränserna finns i
[kommandokontraktet](SLASH_COMMANDS.md).

## Referenser som följer texten

Valet från `@` sparar typ, stabilt ID och ett UTF-16-spann. Ett skrivet namn
utan valt ID blir ingen automatisk referens. Förlaga, beskrivning, checklista
och kommentar behåller sina egna referenser. Att flytta text före ett valt
namn flyttar spannet; att redigera själva namnet tar bort dess bindning.
Läsning och klick kontrollerar aktuell åtkomst till målet. Referenser ger
aldrig nya medlemskap eller andra behörigheter.

Delade dokument lagrar referenser med Yjs relativa positioner i samma
dokumenttillstånd. Samtidiga ändringar före en referens kan därför sammanfogas
utan att namnet binds till fel objekt. Revisioner behåller också referenser.
Flera vyer av samma dokument och session delar en lokal Yjs-skrivare. Aktivt
arbete synkroniseras snabbt; inaktiva ytor backar sin reservpollning och väcks
av kanalens ID-baserade ändringssignal, fokus eller återanslutning.

Projekt, uppgifter, flödesinlägg, beslut, kalenderposter, dokumentlänkar,
klipp, filkommentarer och godkännanden lagrar valfri `field_references`
tillsammans med det befintliga krypterade resursinnehållet. Dokumentkommentarer
använder sin valfria `references`-lista. Tom metadata utelämnas så att äldre innehåll
fortsätter fungera. Nya eller ändrade native-referenser kontrolleras mot
aktuell kanalåtkomst. Oförändrade historiska länkar får finnas kvar när ett
annat fält ändras, även om målet senare blivit otillgängligt; länken ger ändå
ingen åtkomst. Dokumentens Yjs-innehåll följer sitt befintliga dokumentprotokoll.

I Matrix-kanaler ligger arbetsinnehåll och tillhörande referenser i det
krypterade eventet. Ingen okrypterad sidokopia eller native-reserv används.
Det gör inte serverläsbara native-kanaler end-to-end-krypterade.
Det utökar inte vilka verktyg som stöder Matrix; native-only-flöden behåller
sin befintliga gräns. Se [krypteringsimplementationen](E2EE_IMPLEMENTATION.md).

Person- och gruppreferenser i chatmeddelanden använder den befintliga
notisleveransen. En referens i ett autosparat dokument är en länk och betyder
inte att varje tangenttryckning skickar en notis. Se
[referenser och grupper](MENTIONS_REFERENCES.md).

| Arbete | Fält med valda referenser |
| --- | --- |
| Delade dokument | Titel, brödtext och kommentarer; högdiskussioner visar sidornas referenser |
| Projekt och uppgifter | Titlar, beskrivningar och checklistor |
| Flödet | Titel, innehåll, kommentarer, omröstningsfråga och svarsalternativ |
| Beslut | Titel och beslutsunderlag, även i historik |
| Kalender och dokumentlänkar | Kalendertitel, beskrivning och plats; länktitel och anteckningar |
| Filer och klipp | Filkommentarer; klipptitel och transkription |
| Godkännanden och egna utkast | Godkännandets kommentar; privata meddelandeutkast för aktuell kanal |

## Tydliga gränser

- Innehållsfält optas uttryckligen in. Lösenord, API-nycklar, adresser, sökning,
  installationskonfiguration och andra tekniska fält kapas inte av menyerna.
- Gruppnamn, högnamn och andra identitetsetiketter kan öppna kommandon men
  lagrar inte lösa `@`-etiketter utan ett referenskontrakt. Gruppmedlemmar väljs
  fortfarande med verkliga medlems-ID:n.
- Ett fält för en annan kanal i exempelvis integrationsdialogen använder inte
  den aktiva chattens referenskatalog. Fristående SDK-paneler utan en
  samarbetsvärd behåller sina vanliga kontroller.
- AI-instruktioner och externa mötesrubriker kan öppna kommandon men får inga
  nya referens-ID:n eller automatisk källöverföring. AI använder fortfarande
  sin uttryckliga granskning av källor och samtycke till extern behandling.
- Delat arbete mellan installationer använder ett separat krypterat
  federationsprotokoll. `/` öppnar lokala produktverktyg; `@` använder verifierade
  globala deltagar- och resurs-ID:n för typat delat arbete. Native-ID:n eller
  ett namn används aldrig som en påhittad peeridentitet. Se
  [krypterat arbete](ENCRYPTED_WORK.md) och [federationskontraktet](FEDERATED_WORK.md).

## Privat kalender och ursprungligt samtal

`/calendar` öppnar den egna privata kalendern i **Min arbetsdag**. Samma
författarfält använder `/`, `@` och explicit AI-granskning. Sparade referenser
behåller sin ursprungliga kanal. Om kalendern öppnas från en annan kanal visar
den en verifierad **Fortsätt i kanal**-ingång, som behåller samma privata editor,
text och referenser medan chattens sammanhang byts. Andra osparade arbeten
behåller sina vanliga navigationsskydd. Ett nyare text-, IME-, konto- eller
navigationsval ogiltigförklarar en äldre väntande fortsättning.

## Implementation och verifiering

Den gemensamma [inmatningen](../frontend/src/components/CollaborationInput.tsx)
använder samma parser, funktionsregister och referensväljare som chatten.
[Samarbetskontexten](../frontend/src/lib/collaboration-context.tsx) binder
anropet till exakt fält och aktuell auktoritet. Värden i
[Chat](../frontend/src/components/Chat.tsx) öppnar vanliga produktpaneler i
[arbetsytorna](../frontend/src/components/CollaborationWorkbench.tsx).

[Inmatningsprovet](../frontend/tests/collaboration-input-browser.py) täcker
den faktiska React-komponenten. [Arbetsyteprovet](../frontend/tests/collaboration-workbench-browser.py)
kontrollerar behållen editor och Yjs-identitet. [Produktprovet](../frontend/tests/universal-collaboration-browser.py)
använder riktiga disponibla installationer och produktens UI/API för
samtidighet, referenser, behörigheter och mobil. Faktiskt körda resultat och
avgränsningar redovisas separat i [valideringsloggen](VALIDATION.md).
Den separata [dialogkontrollen](../frontend/tests/collaboration-modal-browser.py)
provar behållen DOM, portalernas föräldraordning och återupptaget fokus.


### Writing-field labels

A field label names only its input. `LabeledWritingField` associates a separate
label and stable input ID while keeping command, reference and AI controls
outside that label. Existing input IDs, refs and handlers survive the wrapper.
This prevents helper buttons or expanded AI content from changing the name read
by assistive technology. Keyboard focus and live Swedish labels are covered by
the actual command/reference browser regression.
