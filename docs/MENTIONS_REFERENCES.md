# Personer, grupper och material direkt i samtalet

Skriv `@` där markören står i meddelandet och välj en person, en kanalgrupp,
ett dokument, en uppgift, en fil eller det aktuella samtalet. Valet infogar ett
läsbart namn och binder just det textspannet till objektets verkliga identitet.
Två personer med samma namn förblir två olika val. Vanlig text som
`alex@example.com`, en URL eller ett ofullständigt `@namn` blir ingen automatisk
omnämning.

Listan kan användas med piltangenter och Enter. Escape och Tab lämnar sökningen
utan att skicka meddelandet. Att söka eller välja en referens skapar inget
dokument, ändrar ingen grupp och kör ingen AI eller extern app. Kommandon som
öppnar ett utkast behöver fortfarande vanlig granskning och Spara; se
[kommandon i skrivfältet](SLASH_COMMANDS.md).

## Vad referenserna betyder

| Typ | Verkligt mål och beteende |
| --- | --- |
| `person` | En aktuell medlem i samma kanal. Personens stabila konto-ID styr omnämningen. |
| `group` | En namngiven notisgrupp i samma kanal. Aktuella, fortfarande behöriga medlemmar blir mottagare. |
| `document` | Ett tillgängligt, ej arkiverat delat dokument i samma kanal. Öppnar dokumentets vanliga produktflöde. |
| `task` | En tillgänglig, ej arkiverad uppgift i ett aktuellt projekt i samma kanal. Öppnar den verkliga uppgiften. |
| `file` | En tillgänglig fil i samma kanal. Native använder filens innehålls-ID; krypterade filer verifieras genom den betrodda klienten. |
| `channel` | Det aktuella samtalet. Detta är ingen sökväg till andra eller privata kanaler. |

En referens ger ingen ny behörighet, bjuder inte in någon och ändrar inga
kanal- eller projektmedlemskap. Servern kontrollerar aktuell aktör, kanal och
mål vid native-skrivning. Att följa ett gammalt meddelandes referens kräver
aktuell åtkomst till målet. Om målet har försvunnit eller åtkomsten har ändrats
behålls meddelandets skrivna text, men referensen får inte öppna innehållet.
En otillgänglig identitet besvaras utan namn eller privat målmetadata.

## Riktiga kanalgrupper

En kanaladministratör kan öppna grupphanteringen från samtalet, ange ett namn
och uttryckligen välja befintliga kanalmedlemmar. Andra kanalmedlemmar kan se
grupperna. Dessa grupper är notislistor, inte organisationsgrupper eller en ny
åtkomstmodell.

Skapa kräver aktuellt administratörskap. Redigering och radering kräver dessutom
gruppens `expected_version`; en konkurrerande ändring ger konflikt och behöver
läsas om. Namnet är unikt inom kanalen efter normalisering och jämförelse utan
skillnad på stora och små bokstäver. Avstängda konton och personer som lämnat
kanalen räknas inte som aktuella mottagare. En grupp utan aktuella medlemmar
kan fortfarande granskas i grupphanteringen men kan inte väljas eller skickas
som en användbar omnämning.

Native-sändning expanderar gruppen under kanalens skrivlås. Grupp och explicit
personval slås ihop utan dubbla mottagare. Schemaläggning sparar gruppens ID,
inte en fryst gruppmedlemslista: den faktiska leveransen validerar målet och
expanderar då aktuella medlemmar igen. Om gruppen har raderats eller blivit
otillgänglig får meddelandet inte skickas med gissade eller gamla mottagare.
En redan levererad notis ändras inte när en grupp senare ändras.

## Text, identiteter och redigering

Meddelandet behåller den mänskligt skrivna texten. Referensmetadata innehåller
bara typ, ID och start/slut i texten, utan en extra kopia av målets titel:

```json
{
  "content": "Hej 🧭 @Ada",
  "references": [
    { "kind": "person", "id": "actual-person-id", "start": 7, "end": 11 }
  ]
}
```

Positionerna är **UTF-16-kodenheter**, samma som webbläsarens textfält.
Start är inkluderande och slut exkluderande. Spann får inte överlappa eller
dela ett surrogatpar. Varken server eller klient gissar en identitet från ett
visningsnamn. Ett senare namnbyte skriver inte om historiska meddelanden.

Infogad text före en referens flyttar dess spann. Ändring inne i det valda
namnet tar bort bindningen; användaren kan välja målet igen. Meddelande-
redigering och trådsvar använder samma metadata och aktuell kontroll.
Om en person- eller gruppreferens tas bort räknas mottagarna om. Äldre
native-klienters uttryckliga `mentions`-ID:n stöds fortfarande, men vanlig
`@text` utan en vald identitet skapar ingen ny mottagare.

## Native och klientkrypterade samtal

**Native:** texten går genom produktens befintliga serverkryptering i vila.
Den är serverläsbar vid behörig behandling. Referensernas ID:n och textpositioner
lagras i separata additiva tabeller, inte som extra okrypterade titlar. Detta
gör inte native-kanalen end-to-end-krypterad.

**Matrix/E2EE:** text, valda namn och referensspann ligger i det krypterade
meddelandets innehåll och tolkas på den verifierade enheten. Ingen native-
meddelandekopia används som reserv. Personer och kanalgrupper är vanlig
behörighetskontrollerad katalogmetadata, liksom kanalens medlemslista;
mottagar-ID:n för produktens notiser är inte hemliga meddelandetexter.
Aktuella grupper löses före faktisk krypterad sändning.

Serverns förslagslista dekrypterar inga Matrix-dokument eller uppgiftstitlar.
Den kan verifiera befintliga opaka dokument-/uppgiftsindex i rätt kanal utan
att returnera deras titlar. Klienten hämtar och dekrypterar material genom
produktens vanliga betrodda klienter. Krypterade filer har inget native-
filindex; deras verkliga krypterade event och rum verifieras på enheten.
Kanalen får en generell serveretikett, som bara kan ersättas med redan
verifierat kanalnamn på klienten. Låst eller overifierat innehåll ger inga
påhittade resursförslag.

## Utkast, köer och datalivscykel

Rich-utkast och offlinekön behåller text och referensspann i den befintliga
krypterade, kontobundna lokala lagringen. Konto-, kanal- och krypteringsläge
kontrolleras igen före leverans. En native-offlineleverans använder sitt
beständiga klient-ID för idempotens; samma kvittens kan inte återanvändas för
en annan uppsättning referenser. Gruppmedlemmar avgörs vid leverans.
Otillgängliga mål eller ändrad identitet behöver granskning, inte tyst fallback.

Native-schemaläggning levereras av installationens worker. E2EE-schemaläggning
ligger på den betrodda enheten och behöver en aktiv, ansluten klient när den
ska skickas; den är ingen serverdekrypterad bakgrundskö. Vanliga kögränser och
ett tidsfönster på högst sju dagar gäller fortfarande.

Native-export tar med referens-ID:n/spann och tillåtna kanalgrupper/medlemskap.
Radering av ett meddelande rensar dess referenser, även vid moderering och
fysisk dataradering. Skickad eller avbruten schemaläggning rensar köreferenserna.
Kontoradering tar bort personens gruppmedlemskap. Referenser håller inte kvar
målresurser mot ordinarie gallring. En raderad fil eller ett arkiverat dokument
kan därför lämna läsbar historisk meddelandetext utan en fungerande mållänk.
Matrix-export/radering följer sina befintliga krypterade klient- och eventflöden;
native-export innehåller ingen dekrypterad Matrix-kopia.

## API och praktiska gränser

Rutterna nedan ligger under `/mentions/channels/{channel_id}`. De använder
vanlig autentisering, aktuell kanalbehörighet och privata, ej cachebara svar.

| Metod och suffix | Kontrakt |
| --- | --- |
| `GET /suggestions?query=&kinds=person,group,...` | En lista med `{kind,id,label,channel_id,description?,member_count?,project_id?}`. |
| `POST /references/resolve` | `{references:[{kind,id}]}` → `{items:[{kind,id,available,...}]}`. Otillgängliga mål saknar titel och privat metadata. Tillgänglig person/grupp kan ha aktuella `user_ids`. |
| `GET /groups` | Lista över kanalens verkliga grupper, med `member_ids` och `version`. |
| `POST /groups` | `{name,member_ids}` → skapad grupp. Kräver kanaladministratör. |
| `PATCH /groups/{id}` | `{expected_version,name?,member_ids?}` → uppdaterad grupp. |
| `DELETE /groups/{id}?expected_version=N` | Versionsskyddad radering. |

Meddelande-API:erna accepterar valfri `references`-lista med objekt enligt
exemplet. Saknad lista motsvarar inga typade referenser. Grupper skapar inte
en alternativ meddelande- eller SDK-lagring.

- Högst **50 referenser** och **50 olika mottagare** per meddelande, inklusive
  explicit äldre `mentions`. För många mottagare avvisas; de klipps inte tyst.
- Högst **100 grupper per kanal**, **1–50 olika aktuella medlemmar** vid
  gruppskrivning och **80 tecken** i gruppnamnet.
- Förslag har högst **50 resultat**, sökfråga högst **80 tecken** och etiketter
  högst **200 UTF-16-enheter**. Tom sökning fördelar högst åtta förslag per typ.
- Native-sökning granskar högst de senaste **100** objekten per resurstyp.
  Klientkrypterade uppgifter använder högst åtta projekt och befintliga
  klientlistors gränser. Detta är kontextuella förslag, inte ett komplett
  globalt sökindex eller en garanti att allt äldre material finns i listan.
- Endast samma kanal stöds. Cross-workspace-/cross-channelreferenser,
  automatiska team från e-postdomäner, namnparsning och autonoma notiser ingår
  inte. Externa leverantörsresurser måste först finnas i ett faktiskt stött
  kanalflöde; en fri URL är inte ett verifierat material-ID.

## Implementation och verifiering

Backendens [modell](../backend/app/models/message_references.py),
[behörighet och referensvalidering](../backend/app/services/message_references.py)
och [katalog-/grupp-API](../backend/app/api/mentions.py) används av vanliga
meddelanden, trådar, offlineleverans och schemaläggning. Den publika
[produktdemon](PRODUCT_DEMO.md) använder samma API och en riktig **Design team**-
grupp med befintliga exempelmedlemmar.

[Referenstester](../backend/tests/test_message_references.py) och
[grupp-/behörighetstester](../backend/tests/test_reference_groups.py) täcker
bland annat Unicode, scope, återkallad behörighet efter lås, aktuella
gruppmottagare, offlinekvittens, schemaändringar, export och fysisk radering.
[Klientmodell](../frontend/src/lib/message-references.ts),
[katalogklient](../frontend/src/lib/message-references-api.ts) och
[krypterat meddelandeinnehåll](../frontend/src/lib/secure/message-content.ts)
har separata kontraktstester. [Browserproof](../frontend/tests/commands-references-browser.py)
prövar riktiga native- och demoflöden med privata exempelkonton. Faktisk
Matrix-drift och kundens externa tjänster behåller sina separata
acceptanskrav; ett lokalt protokolltest bevisar inte deras driftmiljö.
