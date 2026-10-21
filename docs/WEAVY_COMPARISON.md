# Weavy comparison after FreeCollab 0.9

All eight prioritized implementation tracks below now have code and private validation. Personal language is selectable (Swedish/English), and the old experimental posts API remains separate from the new production feed. The SDK is locally packable rather than published; native Office saveback has protocol evidence rather than a vendor desktop test. Cloud pickers and the SDK fail closed for server-side access to encrypted channels, with client-encrypted cloud links supported separately. Real provider credentials/public service operation are not claimed.

See [0.9 scope](PRODUCT_090.md), [languages](LANGUAGES.md) and [validation](VALIDATION.md) for the resulting product. The original dated comparison is preserved below so its former gaps are not mistaken for current behavior.

---

# Weavy jämfört med FreeCollab 0.8

Kontrollerat **2026-10-21** mot Weavys aktuella officiella produkt-, dokumentations- och prissidor samt koden i denna arbetskopia. Detta är en funktionsjämförelse, inte ett praktiskt test av Weavy. Dess offentliga changelog visar v32.2.0, daterad 2026-08-26 [11].

De tydligaste luckorna hos oss är **svensk lokalisering, ett bredare Office-/filflöde, riktiga aktivitetsflöden och ett publicerat komponent-SDK**. Chatt, notifieringar, möten och samarbete i dokument finns redan i FreeCollab, med andra avgränsningar.

## Vad de erbjuder och vad vi faktiskt har

| Område | Verifierat hos Weavy | FreeCollab 0.8 och kvarvarande skillnad |
| --- | --- | --- |
| Inbäddning och SDK | Separata webbkomponenter för chatt, feeds, filer, kommentarer, copilot och notifieringar; npm-importer, React-stöd, typade gränssnitt och värdapplikationens tokenfabrik [1, 2]. | Egen React-app, REST-API och en inbäddbar chattwidget finns. Widgeten pollar senaste 50 meddelanden och stöder inte säkra Matrix-kanaler. Vi saknar ett publicerat, versionshanterat SDK med fristående produktkomponenter, värdappens auth-kontrakt och färdiga ramverksexempel. |
| Chatt | Direkt-/gruppchatt, trådar, närvaro, omnämnanden, bilagor och notifieringar [3]. | Redan implementerat, tillsammans med reaktioner, läskvittenser, historiksökning och sparade objekt. Detta ska inte räknas som nya luckor. Skillnaden är främst komponentpaketering och widgetens begränsningar. |
| Aktivitetsflöden | Samlat flöde från flera källor, sökning, följda personer, riktiga inlägg med kommentarer, reaktioner och omröstningar [4]. | Kanaler, trådar, beslut och notifieringshistorik finns. Äldre posts/comments-API:er är experimentella och avstängda som standard; ett färdigt användargränssnitt för publicerade flöden, följande och omröstningar saknas. Ett besluts godkännande är inte samma produktfunktion som en omröstning. |
| Filförhandsvisning | Files-dokumentationen anger över 100 format, inklusive Office, kod och strömmad ljud/video; även miniatyrer, tabell-/rutnätsvy, filkommentarer och versionsåterställning [5]. | Filuppladdning, versionshistorik och nedladdning finns. Inbyggd förhandsvisning är begränsad till PDF och JPEG/PNG/GIF/WebP. Office-filer kan lagras och versionshanteras, men visas inte i en Office-editor. Filmappar, taggar, bred formatvisning och kommentarer direkt på filobjektet är luckor. Kommentarer på våra delade textdokument finns redan. |
| Redigera Office | Weavy dokumenterar en inbyggd WebDAV-server med öppna/redigera/spara tillbaka via exempelvis lokalt Microsoft Word [6]. Produktsidan marknadsför även Office-redigering inne i appen [5]. | Våra nya Yjs-dokument ger samtidig redigering av text, kommentarer och namngivna revisioner. De redigerar inte DOCX/XLSX/PPTX. Vi saknar ett Office-/WebDAV-flöde. Samtidig webbaserad Office-samredigering hos Weavy har inte verifierats i de lästa tekniska sidorna och ska inte förutsättas. |
| Molnfiler | Filväljare för Google Drive, OneDrive, Dropbox och Box; standardmiljön weavy.io har dessa förkonfigurerade [7]. | Google Drive, OneDrive/SharePoint och Nextcloud/WebDAV har redan selektiv tvåvägssynk för vanliga filer i kanaler utan Matrix-kryptering. Det kräver kundens egna anslutningar och ett uttryckligt synkval. Vi saknar motsvarande direkta molnfilväljare samt Dropbox-/Box-adaptrar. En filväljare/länkning är en annan funktion än bakgrundssynk. |
| AI och copilot | Privat kontextuell copilot, @omnämnda agenter, flera modellleverantörer, kunskap från utvalda filer, webbsökning och konfigurerade HTTP-funktioner [8]. | En separat Groq-demochatt finns bakom en avstängd flagga. Den är ingen produktionscopilot för kundens arbetsyta. Vi saknar en färdig kontextuell assistent, behörighetsstyrd kunskapssökning och ett verktygs-/agentgränssnitt. Våra nya arbetsflöden är deterministiska och innebär inte att en AI-agent är byggd. |
| Svenska och andra språk | Engelska och svensk översättning `sv-SE`; fler språk kan läggas till via XLIFF. Separat datum-/tidsformatering stöds [9]. | Produktgränssnittet har huvudsakligen hårdkodad engelsk text. Anpassningsbara färger, typsnitt, densitet och teman finns redan, men full svensk UI-översättning och en sammanhållen i18n-lösning saknas. |
| Notifieringar | Färdiga listor, badges/toasts, navigeringshändelser och webhook för att mata värdappens notifieringssystem. Senaste changelog anger notifieringstaggar och taggfilter [10, 11]. | Notifieringscenter, kanalinställningar, sammanfattningar och nu även valbar Web Push med generiskt innehåll finns. Huvudluckan är komponenterna och adaptern för en extern värdapp, inte att vi saknar notifieringar. Bakgrundspush kräver vår operatörskonfiguration och användarens tillstånd. |
| Möten | Integrering som skapar möten med Google Meet, Microsoft Teams och Zoom, med respektive leverantörs förutsättningar [12]. | Egna WebRTC-möten med mikrofon, kamera, skärmdelning, gästadmission och högst sex enheter är nu implementerade. Kalender-/möteslänkar finns också. Vi saknar motsvarande verifierade OAuth-flöden för att skapa leverantörernas möten. Kalender- och filsynk ska inte räknas som det. |

FreeCollabs sex nya produktdelar är uppgifter/projekt, egna möten, PWA/offline/push, delade Yjs-dokument, arbetsflöden/godkännanden och datakontroller. De ingår i jämförelsen ovan; de ska inte återkomma som förslag att bygga från början. FreeCollab har dessutom en särskild Matrix-väg med klientkrypterat innehåll. De lästa Weavy-sidorna ger inte underlag för att påstå motsvarande verifierad klientkryptering eller för att göra en generell säkerhetsrankning.

## Rekommenderad prioritet

1. **Svenska och en riktig språkinställning.** Översätt hela huvudflödet, hjälpfel, datum och tillgänglighetsnamn. Stor omedelbar effekt för en svensk produkt.
2. **Ett bättre fil- och Office-flöde.** Börja med filorganisation och tydligt öppna/redigera/ladda upp ny version; välj sedan Office-/WebDAV-lösning och formatvisning. Behåll våra delade textdokument som en egen funktion.
3. **Ett publicerbart aktivitetsflöde.** Meddelanden/nyheter, kommentarer, reaktioner, omröstningar och läststatus, med samma kanalbehörigheter som övrig produkt.
4. **Ett modulärt SDK.** Separata chatt-, fil-, dokument- och notifieringskomponenter med versionsstabil auth och exempel. Flytta detta till första plats om FreeCollabs främsta affär är inbäddning i andras produkter.
5. **Direkt molnfilväljare.** Återanvänd befintliga Google-/Microsoft-anslutningar där tillstånden medger det och utvärdera Dropbox/Box. Visa tydligt när man länkar, kopierar eller aktiverar synk.
6. **En valbar produktionscopilot.** Definiera först vilka data användaren uttryckligen delar, modellleverantör och vilka åtgärder som får utföras. Klientkrypterat innehåll ska aldrig skickas till en servermodell som en dold bieffekt.

## Pris och källor

Weavys prissida visade **Pro 249 USD/månad med månadsbetalning**, obegränsade användare och agenter samt 20 procent rabatt vid årsbetalning. Självhostning prissätts efter offert [13]. Agentdokumentationen anger att externa modellleverantörer kräver egna API-nycklar, med undantag för leverantören `weavy` [8]; detta är inte en uppmätt totalkostnad eller belägg för att valfri modellförbrukning ingår. FreeCollab har ingen verifierad jämförbar kommersiell prisplan i denna arbetskopia.

Samtliga länkar lästes 2026-10-21:

1. https://www.weavy.com/ och https://docs.weavy.com/reference/uikit/context
2. https://docs.weavy.com/learn/authentication
3. https://www.weavy.com/product/chat
4. https://www.weavy.com/product/feeds och https://docs.weavy.com/reference/uikit/feed
5. https://docs.weavy.com/reference/uikit/files och https://www.weavy.com/product/files
6. https://docs.weavy.com/learn/integrations/webdav
7. https://docs.weavy.com/learn/integrations/files
8. https://docs.weavy.com/learn/integrations/agents och https://docs.weavy.com/reference/uikit/copilot
9. https://docs.weavy.com/learn/localization och https://docs.weavy.com/learn/styling
10. https://docs.weavy.com/learn/notifications
11. https://docs.weavy.com/resources/changelog
12. https://docs.weavy.com/learn/integrations/meetings
13. https://www.weavy.com/pricing

Underlag för vår sida: `frontend/src/widget.ts`, `FilePreview.tsx`, `WorkHub.tsx`, `backend/app/config.py` samt dokumentationen `PRODUCT_PLAN.md`, `REMOTE_SYNC.md`, `SHARED_DOCUMENTS.md`, `MEETINGS.md`, `MOBILE_OFFLINE.md`, `WORKFLOWS.md` och `DATA_CONTROLS.md`. Befintlig kod och aktuell funktionsdokumentation har företräde framför äldre roadmap-text.
