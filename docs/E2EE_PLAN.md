# Ursprunglig E2EE-plan

Denna plan föregick implementationen. Matrix-spåret har valts och byggts; aktuell arkitektur, konfiguration och begränsningar finns i [E2EE_IMPLEMENTATION.md](E2EE_IMPLEMENTATION.md). Testresultat finns i [VALIDATION.md](VALIDATION.md). Texten nedan bevaras som ursprunglig kravbild.

# Plan för end-to-end-kryptering i FreeCollab

**Status: föreslagen produkt- och arkitekturplan. E2EE är inte implementerat.**

Målet för punkt 8 är att meddelanden krypteras på avsändarens enhet och bara kan läsas på behöriga deltagares betrodda enheter. Servern lagrar och förmedlar krypterat innehåll. Inloggning, kanaltillhörighet och serveråtkomst behövs fortfarande, men ger inte servern innehållets dekrypteringsnycklar.

## Dagens utgångsläge

FreeCollab har serverkryptering av lagrade meddelanden. API:t tar emot klartext, krypterar den med en serverägd nyckelring och dekrypterar historik för behöriga användare. Realtidshändelser innehåller klartext. Filer lagras utan applikationskryptering.

Det finns äldre nyckelfält och hjälpfunktioner, men de utgör inget aktivt E2EE-protokoll:

- Registreringen skapar användarens privata nyckel på servern. Servern tar också emot inloggningslösenordet.
- Kanalnycklar skapas på servern; klienten krypterar inte skickade meddelanden med dem.
- En accepterad inbjudan lägger till medlemskap utan ett klientstyrt nyckelutbyte.
- Borttagning av en medlem spärrar åtkomst, men genomför ingen klientstyrd kryptografisk nyckelrotation.
- Kanalnamn, filnamn och annan metadata lagras i klartext.

Det innebär att befintliga fält som `encrypted`, `encrypted_private_key` och `encrypted_channel_key` inte får användas som bevis för E2EE. Se [säkerhetsmodellen](SECURITY_MODEL.md), [nyckelhanteringen](KEY_MANAGEMENT.md), [meddelande-API:t](../backend/app/api/messages.py), [registreringen](../backend/app/api/auth.py) och [fil-API:t](../backend/app/api/files.py).

## Vad punkt 8 ska omfatta

| Innehåll | Mål |
| --- | --- |
| Kanalmeddelanden och direktmeddelanden | Krypteras på klienten före överföring. Ingen tyst återgång till klartext. |
| Trådar och redigeringar | Samma skydd och verifiering som ursprungsmeddelandet. |
| Reaktioner, fästa meddelanden och beslutsinnehåll | Skyddas som krypterade applikationshändelser. |
| Bilagor och förhandsvisningar | Filbytes, miniatyrbilder och känsliga filuppgifter krypteras före uppladdning. |
| Kanalnamn och kanalbeskrivning | Ingår i krypterade kanaluppgifter. Detta är ett uttryckligt krav, inte en automatisk följd av krypterad meddelandetext. |
| Återställningsmaterial och sökindex | Serverlagrade kopior ska vara krypterade med klientkontrollerade nycklar. |

Krypterad kanalmetadata behöver visas och sökas efter upplåsning på klienten. Servern använder ogenomskinliga kanalidentifierare för routning och behörigheter. Material som användaren uttryckligen publicerar offentligt behöver en separat tydlig produktmodell; allmän publicering ger ingen sekretess gentemot allmänheten.

Servern kommer normalt fortfarande att känna till konton, enheter, medlemskap eller leveransmottagare, IP-adresser, tidpunkter och storlekar. Att dölja dessa trafik- och åtkomstuppgifter kräver ytterligare arkitektur. Produkten ska beskriva **vilket innehåll som krypteras**, och inte lova att all metadata eller användarnas identitet är osynlig.

## Protokollval före implementation

Ett etablerat kryptografiskt bibliotek är inte samma sak som ett färdigt säkert meddelandeprotokoll. FreeCollab bör inte bygga ett nytt grupprotations- och enhetsprotokoll runt de äldre kanalnyckelfunktionerna.

| Spår | Fördel | Konsekvens och beslutspunkt |
| --- | --- | --- |
| Matrix med browser-SDK och Rust-kryptografi | Etablerat ekosystem för krypterade grupper, flera enheter, verifiering och nyckeldelning. | Utvärdera en Matrix-homeserver som meddelandebakplan. SDK och kryptolivscykel är knutna till Matrix-protokollet; det är inte en enkel ersättare för några FastAPI-anrop. |
| MLS, RFC 9420, med vald implementation | Standardiserad gruppnyckelhantering med framåtsekretess och återhämtning efter kompromettering genom säkra uppdateringar. | Passar ett eget leveranssystem, men behöver kompletteras med identiteter, enheter, transport, beständig lagring, synkronisering och återställning. Browser-/WASM-stödet måste provas. |

**Rekommenderat första steg:** utvärdera Matrix som ett komplett meddelandebakplan om en ytterligare tjänst accepteras. Om FreeCollabs egen FastAPI-transport ska behållas, utvärdera MLS i en avgränsad browserprototyp. OpenMLS och mls-rs är implementationskandidater att undersöka, inte färdigvalda beroenden.

Ingen aktuell paketversion, integrationsrevision eller fullständig säkerhetsgranskning har verifierats i denna plan. Vägvalet ska dokumentera:

1. Underhåll, licens, granskningar och säkerhetsuppdateringar för den exakta version som väljs.
2. Funktion och prestanda i de webbläsare och mobila enheter som produkten stöder.
3. Hur enheter, verifiering, offlineanvändning och gruppändringar fungerar i praktiken.
4. Hur krypterade kanalnamn, bilagor, trådar och återställning stöds.
5. Vilka protokollegenskaper som faktiskt uppnås. Matrix/Megolm får exempelvis inte beskrivas som om det automatiskt ger alla MLS-egenskaper för varje bevarad historiknyckel.

En prototyp och granskning av FreeCollabs integration behövs innan produkten marknadsförs som verifierad E2EE. En granskning av ett bibliotek täcker inte automatiskt appens behörigheter, nyckellagring och återställningsflöden.

## Enheter, identitet och nycklar

- Varje enhet får egna nycklar som skapas på enheten. Servern får inga okrypterade privata nycklar.
- Ny enhet godkänns från en redan betrodd enhet eller genom ett definierat återställningsflöde. Ny inloggning ska inte ensam ge tillgång till gammal krypterad historik.
- Deltagare kan verifiera varandra med QR-kod eller säkerhetskod. Ändrade identiteter och overifierade enheter visas tydligt.
- Servern får inte kunna byta ut en publik nyckel och få klienten att automatiskt lita på den. Protokollets identitetsmodell och betrodda enheters godkännande måste genomdrivas.
- Enhetslistan visar verifieringsstatus och senaste användning, med möjlighet att återkalla en förlorad enhet.
- Nycklar ska skyddas i klientens lämpliga lagring. Råa privata innehållsnycklar ska inte placeras i `localStorage`, loggar eller analysdata. Att en nyckel inte kan exporteras skyddar inte i sig mot skadlig kod som kan begära dekryptering.

Krypterade rum får ett uttryckligt format och protokollversion. Klienten ska verifiera avsändare, kanal-/gruppkontext och medlemskapsepok innan innehållet visas. Manipulerade, återspelade eller obehöriga meddelanden får inte behandlas som vanlig klartext vid ett dekrypteringsfel.

## Inbjudningar, historik och medlemshantering

En inbjudan ger servermedlemskap. Tillgång till innehållets nycklar ges därefter genom protokollets verifierade anslutningsflöde. Om ingen betrodd deltagarenhet kan slutföra det ska användaren se ett tydligt vänteläge.

För nya medlemmar föreslås åtkomst till meddelanden från inträdet som standard. Delning av äldre historik ska vara ett separat uttryckligt val, med en tydlig förklaring av vad som delas. Det ska inte avgöras av att servern råkar ha äldre krypterade rader tillgängliga.

När en medlem eller enhet tas bort ska serveråtkomst spärras direkt och det kryptografiska medlemskapet uppdateras. Framtida meddelanden använder den nya epoken enligt protokollet. Gamla meddelandekopior som mottagaren redan har läst, sparat eller exporterat kan inte tas tillbaka.

Samtidiga medlemsändringar, offlineenheter och sändningar under rotation måste hanteras. Ett meddelande med en föråldrad epok ska avvisas eller säkert skickas om efter uppdatering. Servern ska aldrig skapa en ersättningsnyckel för att kringgå ett väntande klientutbyte.

## Kontoåterställning och innehållsåterställning

Lösenordsåterställning återställer tillgången till kontot. Den återskapar inte automatiskt nycklar till gammalt innehåll.

Innehåll kan återställas från en betrodd enhet eller en separat stark återställningsnyckel som användaren sparar. Servern kan förvara en krypterad säkerhetskopia av nyckelmaterialet, men får inte kunna låsa upp den. En kopia skyddad enbart med ett lösenord som samma server tar emot ger inte den avsedda gränsen mot serveroperatören.

Användaren behöver kunna skapa och kontrollera sin återställningskopia, godkänna en ny enhet och återkalla en gammal. Om alla betrodda enheter och allt återställningsmaterial förloras kan äldre innehåll bli oåterställbart. Den konsekvensen ska visas vid konfigurering och kunna övas i ett återställningsflöde.

En administratörsnyckel som kan dekryptera all historik är nyckeldeponering och ändrar sekretessmodellen. Den ska inte införas som en dold reservväg.

## Konsekvenser för punkterna 1–7

| Funktion | Utformning tillsammans med E2EE |
| --- | --- |
| Kontohantering | Skilj kontoinloggning och MFA från godkännande av enheter och innehållsåterställning. |
| Fullständig sökning | Dekryptera och indexera behörig historik lokalt. Nya enheter behöver synkronisera historik och bygga sitt index. Ett komplett serversökindex med klartext skulle bryta sekretessmålet. |
| Notiser och omnämnanden | Push och e-post visar som standard att ny aktivitet finns, utan meddelandetext. Omnämnanden tolkas på klienten; eventuell routningsinformation måste begränsas och dokumenteras. |
| Arbetsytor och gäster | Serverrollen och det kryptografiska medlemskapet behöver hållas överens. Administratörsrollen ger inte automatiskt tillgång till alla privata kanalnycklar. |
| Filer och versioner | Varje filversion och förhandsvisning krypteras före uppladdning. Nyckeln delas inom det krypterade samtalet. Förhandsvisning sker på behörig klient. Servern kan inte göra vanlig innehållsskanning av bytes som den inte kan dekryptera. |
| Bokmärken och beslut | Text och känsliga sammanfattningar ska inte kopieras till oskyddade serverfält. Personliga bokmärken och delade beslut behöver separata åtkomstregler. |
| Direktmeddelanden | Bygg på samma verifierade enhets- och kanalprotokoll. Skriv inte ett separat enklare kryptosystem för två personer. |

Server-AI, externa sammanfattningar och innehåll i e-post behöver ett uttryckligt produktval. Att låta en server eller extern leverantör dekryptera ett rum är en ändrad förtroendegräns, inte en osynlig detalj.

En eventuell OTRUST-funktion kan arbeta med genomtänkta innehållsåtaganden, men en filhash eller tidsstämpel krypterar inget. Att dela ett förutsägbart dokuments hash kan också avslöja vilket dokument det gäller. Integrationen behöver en egen modell för vad som lämnar klienten.

## Webbläsarens förtroendegräns

En webbklient behöver lita på den programkod som körs på enheten. En operatör som kan ersätta levererad JavaScript-kod kan försöka stjäla klartext eller nycklar i klienten. Krypterad databaslagring och TLS förhindrar inte detta.

Förstärk klienten med begränsad scriptpolicy, skydd mot kodinjektion, kontrollerade beroenden och granskade releaser. Om hotmodellen även omfattar en aktiv operatör som ändrar webbappen behövs en starkare distributionsmodell, exempelvis oberoende verifierade signerade klienter. E2EE får inte beskrivas som ett fullständigt skydd mot en komprometterad deltagarenhet.

Den nuvarande widgeten skickar klartext till servern. En uppdaterad E2EE-widget behöver samma protokoll och godkända enheter, och värdsidans skript blir en del av klientens förtroendegräns. Äldre klienter ska nekas att skicka klartext i E2EE-kanaler med en begriplig uppgraderingsanvisning.

## Migrering av befintligt innehåll

Kryptering kan inte retroaktivt göra tidigare serverläst historik hemlig för servern.

Rekommenderad första leverans är nya E2EE-kanaler med tydlig status och startpunkt, medan äldre historik visas med korrekt märkning. Om historik ska flyttas behöver en behörig betrodd klient hämta innehållet, kryptera om det och kontrollera att meddelanden, trådar och filer har följt med. Äldre avsändaruppgifter kan inte upphöjas till ny kryptografisk avsändarverifiering i efterhand.

Migreringen ska kunna återupptas efter avbrott, undvika dubletter och verifiera resultat innan någon äldre kopia tas bort. Säkerhetskopior kan fortfarande innehålla tidigare klartextfiler eller servernycklar; deras faktiska retention och borttagning behöver hanteras separat. Ingen databasflagga får ändras till E2EE utan att innehållet verkligen har konverterats och verifierats.

## Leveransordning och godkännandekriterier

1. **Hotmodell och protokollbeslut.** Bestäm vad servern får veta, hur betrodda enheter verifieras och vilka återställnings- och historikregler som gäller. Prova kandidater i två oberoende webbläsare.
2. **Enheter och återställning.** Skapa enhetsnycklar lokalt, verifiera enheter och genomför ett riktigt återställningsprov. Verifiera att en vanlig lösenordsåterställning inte låser upp gammalt innehåll.
3. **Krypterade kanaler och meddelanden.** Skicka, läs, redigera och svara i trådar utan innehåll i serverns API, realtidsdata, databas eller loggar. Krypterade kanaluppgifter ska fungera i navigationen.
4. **Medlemskap och flera enheter.** Prova inbjudan, offlineanslutning, vald historikdelning, återkallad enhet och borttagen medlem under samtidiga sändningar.
5. **Filer och produktfunktioner.** Genomför krypterad uppladdning, nedladdning, förhandsvisning, versionering, lokal sökning och notiser utan oavsiktlig innehållsexponering.
6. **Migrering och återhämtning.** Testa återupptagen migrering, blandad äldre historik, säkerhetskopior och återställning på en ny enhet. Äldre klienter ska misslyckas säkert i nya krypterade kanaler.
7. **Integrationsgranskning och pilot.** Granska protokollanvändning, enhetsförtroende, nyckellagring och appens kodvägar. Dokumentera verifierade egenskaper och kvarvarande gränser före bredare användning.

Kontrollerna ska bland annat försöka byta ut mottagarnycklar, manipulera krypterat innehåll, återspela meddelanden, använda fel kanal, återanvända en gammal epok och ansluta en återkallad enhet. En serverkopia utan klienternas hemligheter ska sakna möjlighet att dekryptera det nya innehållet. Ett godkänt normalt chattflöde räcker inte som verifiering av dessa egenskaper.
