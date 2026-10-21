# FreeCollab: produkt- och genomförandeplan för funktionerna 1–8

Status: arbetskrav för den nu byggda 0.5-kandidaten. Konton, arbetsytor, historiksökning, notiser, filversioner, sparade poster, beslut och direktmeddelanden har implementerats. Klientkrypteringen använder Matrix med Rust-kryptografi; integrationsverifieringen beskrivs separat i [E2EE_IMPLEMENTATION.md](E2EE_IMPLEMENTATION.md) och [VALIDATION.md](VALIDATION.md). Detaljerna nedan behålls som acceptanskrav och framtida utbyggnad, inte som ett påstående att varje framtidsönskemål är levererat.

Målet är en sammanhållen samarbetsprodukt med personligt utseende, fungerande teamflöden och end-to-end-kryptering. 0.5 använder separata arbetsytor och en privat Matrix-tjänst för klientkrypterade kanaler. Listorna nedan beskriver produktkraven; verifieringsdokumentet skiljer genomförda kontroller från sådant som återstår inför en bredare lansering.

## Gemensamma produktbeslut

- Ett konto kan tillhöra flera arbetsytor. Installationens operatör, arbetsytans ägare och kanalens administratör har skilda ansvar.
- Behörighet och krypteringsnycklar är separata. En administrativ roll ger inte automatiskt tillgång till en kanals klartext.
- Kontoåterställning kan återställa inloggningen. Krypterad historik kräver dessutom en redan betrodd enhet eller en användarägd återställningsnyckel.
- En inbjudan ger inte tillgång till alla kanaler eller automatiskt till äldre krypterad historik. Historikdelning och enhetsgodkännande ska vara uttryckliga.
- Nyckelbyte vid borttagning skyddar framtida innehåll. Redan nedladdade meddelanden eller filer kan inte tas tillbaka kryptografiskt.
- Den gamla pilotens serverkryptering ska fortsätta beskrivas korrekt under en övergång. Ingen kanal ska visas som end-to-end-krypterad innan det gäller dess verkliga dataflöden.

## 1. Kontohantering och säker återställning

### Produktbeteende

Användaren ska kunna verifiera sin e-post, återställa ett glömt lösenord, byta lösenord och se eller återkalla sina inloggade sessioner. Därefter tillkommer tvåfaktorsinloggning med en autentiseringsapp och engångskoder för återställning.

### Första leveransen

- Verifierings- och återställningslänkar med kort giltighet, engångsanvändning och endast tokenhashar i databasen.
- Svar som inte avslöjar om ett konto finns, begränsad omsändning och en beständig e-postutkorg med återförsök.
- Återkallande av befintliga sessioner efter säkerhetskänsliga ändringar, även för redan anslutna klienter.
- Tydliga kontosidor för e-post, lösenord och sessioner. Känsliga åtgärder kräver ny autentisering.
- Konfigurerad e-postleverans för den egna installationen; lokala tester använder en isolerad e-postmottagare.

0.5 inkluderar också TOTP, säkert införande/avaktivering och hashade engångskoder. Kontots engångskoder för inloggning ska inte förväxlas med krypteringens återställningsnyckel.

### Godkänt när

Återanvända och utgångna länkar nekas, samtidiga försök bara kan använda en token en gång, gamla sessioner upphör och fel i e-postleveransen inte visas som lyckad leverans. Lösenordsåterställning får aldrig påstå att krypterad historik är återställd utan tillgång till dess nycklar.

## 2. Fullständig historik och sökning

### Produktbeteende

Användaren ska kunna läsa äldre historik och söka i det innehåll som hen har rätt att dekryptera, med filter för kanal, person, datum och innehållstyp. Resultat öppnar rätt meddelande och dess sammanhang.

Den tidigare klienten sökte främst bland de senaste 50 inlästa meddelandena. 0.5 laddar äldre tillåten historik och söker lokalt efter synkronisering; en ny enhet kan bara söka innehåll vars nycklar den har tillgång till.

### Leveranser

- Ladda äldre historik utan att hoppa i läspositionen; fyll luckor efter frånkoppling.
- Synkronisera ändringar, raderingar och trådar, även efter att klienten varit frånkopplad.
- Bygg ett lokalt sökindex efter dekryptering. Visa vad som är synkroniserat och vilken historik som återstår.
- Sök i meddelanden, sparade poster och dekrypterade filnamn. Innehållssökning i dokument blir en separat klientfunktion.
- Håll lokal data och eventuella indexnycklar åtskilda per konto och enhet; rensa eller lås dem när åtkomst upphör enligt den valda enhetsmodellen.

### Godkänt när

Innehåll äldre än det första historikfönstret hittas efter synkronisering, resultat uppdateras efter redigering/radering och ett kontobyte aldrig visar föregående kontos resultat. En E2EE-server ska inte ta emot sökorden eller lagra ett klartextindex. En ny enhet kan bara söka den historik vars nycklar den har fått.

## 3. Omnämnanden, trådbevakning och notiser

### Produktbeteende

Användaren ska kunna välja rätt person med `@`, bevaka en tråd, tysta en kanal och få en samlad inkorg för sådant som behöver uppmärksamhet. Notisnivåer bör omfatta all aktivitet, omnämnanden och tyst läge.

0.5 kopplar medlems-ID-baserade omnämnanden, trådbevakning och notisnivåer till den ordinarie chatten. Experimentella inlägg/kommentarer förblir avstängda som standard. Krypterade kanalnotiser använder generiskt innehåll och verifierade händelsereferenser.

### Leveranser

- Medlems-ID-baserade omnämnanden med personväljare, inte tolkning av ett otydligt visningsnamn.
- Bevakning/avbevakning av trådar och kanalvisa notisnivåer.
- En gemensam händelsemodell med dubblettskydd per mottagare och händelse; egna aktiviteter filtreras.
- Generiska e-postsammanfattningar med aktivitet och länkar som ett senare steg, när e-postleveransen från punkt 1 fungerar.
- Krypterade omnämnandemarkeringar i innehållet och ett uttryckligt beslut om vilken mottagarinformation servern får se för routning.

### Godkänt när

En borttagen medlem inte får nya notiser för kanalen, inställningar faktiskt styr nya händelser och återförsök inte ger dubletter. Servergenererade mejl får inte innehålla dekrypterad E2EE-text. Det ska vara tydligt vilken avsändar-, mottagar- eller aktivitetsmetadata routningen behöver.

## 4. Arbetsytor, team och gäster

### Produktbeteende

Ett team ska ha ett eget namn, logotyp, personkatalog och administration. Användaren ska enkelt kunna växla mellan arbetsytor. Gäster får endast se de kanaler som de uttryckligen har bjudits in till.

### Leveranser

- Arbetsyta och medlemskap som egna modeller med rollerna ägare, administratör, medlem och gäst.
- Arbetsytebundna kanaler, inbjudningar, personval och direktmeddelanden.
- Namn, logotyp och gemensam grundprofil; användarens personliga färger och typsnitt fortsätter vara individuella.
- Säkert ägarbyte och begränsningar som hindrar att den sista ägaren lämnar utan överlämning.
- En explicit migration som placerar befintliga kanaler och medlemmar i en standardarbetsyta utan att skapa nya åtkomsträttigheter till privat innehåll.

Nuvarande databasstart skapar saknade tabeller men ändrar inte befintliga kolumner. Schemaändringar behöver därför faktisk migrationskod, säkerhetskopia och testad återställning.

### Godkänt när

Arbetsytor är isolerade även vid direkta API-anrop och anslutna socketar. Gäster kan inte söka fram andra personer eller kanaler genom personväljare, sökning, länkar eller notiser. Arbetsyteadministration får inte bli en bakdörr till E2EE-nycklar.

## 5. Filer, förhandsvisning och versioner

### Produktbeteende

Användaren ska kunna dra in flera filer, följa uppladdningen och förhandsvisa bilder eller PDF:er direkt i samtalet. Ett dokument kan ha flera tydliga, oföränderliga versioner med uppladdare och datum.

### Leveranser

- Drag-and-drop, uppladdningskö, förlopp, avbrytning och återförsök med dagens kanalbehörigheter och storleksgräns som grund.
- Säkra lokala bild- och PDF-förhandsvisningar, tangentbordsstyrning och tydliga nedladdningsval.
- Separera dokumentidentitet från filversion. Nya versioner ska inte skriva över de gamla filernas bytes.
- Kryptera filen och känslig metadata före uppladdning vid E2EE. Filnyckeln delas i ett krypterat kanalmeddelande.
- Bevara versionsspecifika innehållsreferenser så att länkar och eventuella framtida bevis pekar på exakt rätt version.

### Godkänt när

Gamla versioner kan hämtas med oförändrade bytes, avbrutna försök inte visas som uppladdade och obehöriga nekas även för förhandsvisningar. Dekrypterade Blob-URL:er återkallas när vyn stängs eller kontot byts. Servern får inte ta emot klartext för att skapa miniatyrer i en E2EE-kanal.

## 6. Fästa meddelanden, privata bokmärken och beslut

### Produktbeteende

Teamet ska kunna fästa viktiga meddelanden i kanalen. Användaren ska kunna spara sådant som bara hen själv vill återkomma till. Beslut ska gå att hitta utan att läsa om hela samtalet.

### Leveranser

- Gemensamma kanalpins med tydliga rättigheter och privata bokmärken med en egen vy.
- En beslutslogg med titel, beslut, ansvarig, datum, status och länk till diskussionen.
- Versionshistorik för beslut; ändringar ska vara synliga och tidigare versioner tydligt åtskilda.
- Krypterade beslut och känsliga anteckningar. Privata bokmärken behöver en egen användar-/enhetsnyckelmodell så att de inte delas som kanaldata.
- Möjlig senare OTRUST-koppling till en exakt dokument- eller beslutsversion. Ingen klartext ska skickas dit som en bieffekt av att spara ett beslut.

### Godkänt när

Privata bokmärken inte blir teamets data, medlemskap kontrolleras när referenser öppnas och raderat eller otillgängligt innehåll får ett begripligt läge. En ändrad version får inte ärva en tidigare versions bevisstatus. En tidsstämpel ska inte beskrivas som bevis för juridisk identitet eller som kryptering.

## 7. Direktmeddelanden

### Produktbeteende

Användaren ska kunna hitta en person inom sin tillåtna arbetsyta, välja ”Skriv meddelande” och komma till ett befintligt eller nytt privat samtal. Utkast, trådar, filer och personliga teman ska fungera på samma sätt som i kanaler.

### Leveranser

- Personväljare med arbetsytans behörighetsgränser och en separat vy för privata samtal.
- Ett idempotent ”öppna samtal” med exakt två deltagare och en unik deltagarparning inom arbetsytan.
- Begränsa inbjudningar och medlemsändringar så att ett direktmeddelande inte tyst blir en gruppkanal.
- Tydlig personlig arkivering och blockering. Gruppkonversationer skapas uttryckligen som ett annat flöde.
- Samma enhetsverifiering, nyckelåterställning och krypteringsstatus som i övrig chatt.

0.5 inför den kanoniska deltagarparningen och personflödet ovan, inklusive personlig arkivering och blockering. Kanaltypen `direct` ensam gav inte dessa garantier i den tidigare versionen.

### Godkänt när

Samtidiga försök att starta ett samtal leder till samma konversation, en tredje deltagare inte kan läggas till och användare inte kan upptäcka personer utanför sin behörighet. Arkivering och blockering får inte lova att innehåll som redan har lästs kan tas tillbaka.

## 8. End-to-end-kryptering av meddelanden och kanalinnehåll

### Produktlöfte

Meddelanden krypteras på avsändarens enhet och kan bara dekrypteras på godkända deltagarenheter. Servern får förmedla och lagra krypterade data, men ska inte få innehållsnycklarna eller klartexten.

Det ska omfatta kanalmeddelanden, direktmeddelanden, trådar, redigeringar, bilagor, känsliga kanalfält och beslut. Krypterade kanalnamn och beskrivningar behöver uttryckligt stöd; ett protokoll som bara krypterar meddelanden räcker inte för det löftet.

### Nycklar, enheter och medlemskap

- Skapa privata nycklar i klienten. Dagens servergenererade nyckelfält är inte en E2EE-identitet och ska inte återanvändas som om de vore det.
- Skilj kontots identitet från dess enheter. En ny enhet ska kunna godkännas med en betrodd enhet eller en kontrollerad återställningsprocess.
- Visa och verifiera enhetsfingeravtryck med QR-kod eller säkerhetskod; hantera nyckelbyten synligt.
- Byt relevant gruppnyckelmaterial vid medlems- och enhetsändringar enligt valt protokoll. Borttagna medlemmar ska inte kunna läsa framtida innehåll.
- Krypterad nyckelbackup med användarägd återställningsnyckel. Utan betrodd enhet eller återställningsnyckel kan historiken vara förlorad även om kontot går att återställa.

### Teknisk väg och leveransgräns

0.5 använder Matrix JS SDK 44 och dess Rust-kryptografi med en privat Synapse-tjänst bakom FreeCollabs autentiserade gateway. FastAPI/Socket.IO finns kvar för den äldre kanalmodellen och arbetsytefunktionerna. Verkliga separata webbläsare har verifierat krypterade meddelanden, filer, återställning och medlemsborttagning; den exakta omfattningen finns i [VALIDATION.md](VALIDATION.md).

Ett produktionslöfte får inte bygga på en egen kombination av låg­nivåprimitiver utan protokollgranskning. Integrationen har ännu inte fått en oberoende säkerhetsgranskning. [E2EE_PLAN.md](E2EE_PLAN.md) behåller den ursprungliga beslutsplanen; [E2EE_IMPLEMENTATION.md](E2EE_IMPLEMENTATION.md) beskriver genomförandet.

### Övergång från dagens data

Äldre kanaler har serverläsbara meddelanden och filbytes. De behåller sin tydligt märkta säkerhetsmodell; nya tomma privata kanaler kan använda klientkryptering. Historiken konverteras inte automatiskt. Ny kryptering gör inte gamla säkerhetskopior eller tidigare serveråtkomst till end-to-end-krypterade data i efterhand.

### Godkänt när

- Två separata klienter kan kommunicera och en godkänd ny enhet kan återställa avsedd historik.
- API, socketar, databas, filvolym och normala serverloggar saknar meddelandenas, bilagornas och skyddade kanalfälts klartext och användbara innehållsnycklar.
- Förfalskat innehåll, fel avsändare/kanal, otillåtna nyckelbyten och protokollrelevanta återspelningar hanteras enligt valt protokoll.
- Borttagna medlemmar/enheter inte får nycklar för framtida innehåll; inbjudningar och historikdelning följer den beslutade policyn.
- Lokal sökning, notiser, filversioner, bokmärken och återställning fungerar utan en serverbaserad klartextväg.
- Protokollimplementation, nyckellivscykel och migration är granskade innan E2EE anges som produktionsklar.

Servern kommer fortfarande att behöva viss metadata, exempelvis identiteter för behörighetskontroller, tekniska kanal-ID:n, storlekar och leveranstider. Exakt synlighet beror på protokollet. E2EE skyddar inte en redan komprometterad deltagarenhet eller förhindrar mottagaren från att kopiera innehåll. En webbklient måste dessutom lita på den klientkod som levereras till den; starkare skydd mot en aktivt illvillig webboperatör kräver en separat klientdistributionsmodell.

## Rekommenderad genomförandeordning

| Etapp | Konkret leverans | Måste vara klar före nästa beroende |
| --- | --- | --- |
| A: grundbeslut | Arbetsyte-/innehållsidentiteter, medlemskapsmodell, historikpolicy och E2EE-prototyp med vald protokollväg. | Validerad nyckel-, enhets- och återställningsmodell. |
| B: konto och team | Punkt 1 och 4: kontoflöden, sessionskontroll, arbetsytor, roller och verklig datamigration. | Isolerade arbetsytor och separat konto-/nyckelåterställning. |
| C: krypterad kommunikation | Punkt 8 och 7: kanal-/direktmeddelanden, enheter, nyckeldelning och säker medlemsändring. | Två klienter, ny enhet, borttagning och återställning verifierade. |
| D: samarbete | Punkt 3, 5 och 6: notiser, lokala filförhandsvisningar/versioner, pins, bokmärken och beslut. | Ingen dold klartextväg i funktionerna eller deras bakgrundsjobb. |
| E: historik och sökning | Punkt 2: komplett tillåten synk, klientindex, filter och återgång till sammanhang. | Sökomfattning och borttagen åtkomst verifierade även över kontobyten. |
| F: pilot med den nya modellen | Återställning, migrerad historik, widget/klientkompatibilitet och oberoende granskning. | Dokumenterade verkliga resultat före bredare lansering. |

Etapp A–E har nu en implementerad 0.5-kandidat med dokumenterade tester. Etapp F kräver fortfarande oberoende granskning och driftvalidering inför en bredare lansering. Automatisk omkryptering av gammal historik och full widgetkompatibilitet med E2EE ingår inte i den nuvarande leveransen.
