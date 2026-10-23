# Granskning: AI, arbetsflöden och appplattform

Granskad 2026-10-23 mot `c276240` och de avgränsade rättningarna i denna granskning. Underlaget är samtalets beslut om kontextuell AI, Grok, varningar, mindre skrivande och godkända automationer samt [Copilot](../COPILOT.md), [orkestrering](../AI_ORCHESTRATION.md), [arbetsflöden](../WORKFLOWS.md), [appplattform](../APP_PLATFORM.md), [0.9](../PRODUCT_090.md), [0.11](../PRODUCT_011.md) och [Weavy-jämförelsen](../WEAVY_COMPARISON.md). Äldre milstolpstext räcker inte som belägg för fungerande produktbeteende.

**Implementerat** betyder en nåbar väg genom produktens UI, API och verkliga lagring/adapter/worker med relevant teststöd. Det betyder inte att en kunds externa konto är anslutet. **Endast förberett** betyder exempel, konfiguration eller arkitekturskiss utan den utlovade fullständiga körningen. **Saknas** betyder att den efterfrågade förmågan inte finns i den granskade produkten. **Externt overifierat** gäller verklig leverantörsacceptans, drift eller kapacitet som privata protokolltester inte kan bevisa.

## AI och Copilot

| ID | Förmåga/beslut | Status | Konkret väg och verifiering | Begränsning |
| --- | --- | --- | --- | --- |
| AI01 | Kundägd AI-anslutning i den egna installationen | Implementerat | [Inställningar][cop-ui] → [konfiguration/medgivande][cop-api] → krypterad `CopilotConfig`; [konfigurationstester][cop-tests]. | Operatören måste aktivera `COPILOT_ENABLED`; en personlig arbetsyteadministratör konfigurerar med lösenord/MFA. Ingen central FreeCollab-nyckel. |
| AI02 | Individuellt medgivande till den granskade leverantören | Implementerat | [API][cop-api] och [kontextuell komponent][context-ui]; tester för strikt `expected_config_revision`, ändrad leverantör och återkallat medgivande i [Copilot-tester][cop-tests]. | Ett medgivande i en annan flik får inte byta mottagare för en gammal granskning. Ändring av konfiguration ogiltigförklarar tidigare medgivande. |
| AI03 | OpenAI-kompatibel verklig AI | Implementerat | [HTTPS/SSE-adapter][cop-service], [riktiga privata TLS-tester][cop-tests]. | Publik HTTPS443, Bearer-auth, Chat Completions, en choice, 2 048 begärda tokens, 30 sekunder och begränsade svar. Inte valfri modell/protokoll. |
| AI04 | OpenAI-modeller som enkelt val | Implementerat | [Presets][presets] för GPT-4o mini/GPT-4.1 mini → samma sparade arbetsyteleverantör; [preset-tester][preset-tests]. | Preset förbereder ett osparat formulär. Konto, API-rättigheter och fungerande modell måste kunden ha. |
| AI05 | Grok/xAI som enkelt val | Implementerat | [Preset][presets] `api.x.ai`/`grok-4.20-0309-non-reasoning`; [webbläsartest][cop-browser] och [protokollbeskrivning](../COPILOT.md#configure-a-provider-and-agents). | `end_turn` är en mellanmarkör; slutlig `stop`/`tool_calls` och `[DONE]` krävs fortfarande. Ingen automatisk leverantörsväxling. |
| AI06 | Skarpa OpenAI/xAI-kundanslutningar | Externt overifierat | Adaptern och preset-formulären har privata TLS/SSE-bevis, beskrivna i [Copilot](../COPILOT.md). | Ingen riktig kundnyckel, leverantörsdebitering eller skarp xAI/OpenAI-completion har använts som acceptanstest här. |
| AI07 | Namngivna `@agent`-assistenter | Implementerat | [Agentmodell och fast verktygsregister][cop-service], [composern][cop-ui], [API-validering][cop-api], [tester][cop-tests]. | Högst åtta konfigurerade agenter; egna instruktioner/verktygsrättigheter men samma modell/endpoint per arbetsyta. |
| AI08 | Uttryckligt val av meddelanden, textfiler, dokument och klipptranskript | Implementerat | [Klientval och verifierad förberedelse][cop-client] → [serverns källupplösning][cop-service]; [fil/dokument/secure-tester][cop-tests] och [klipptester](../../backend/tests/test_clips.py). | Högst åtta källor/24 000 kontexttecken. Filer måste vara stödda, små text/JSON-filer. Ingen dold inläsning av hela arbetsytan. |
| AI09 | Äldre meddelanden och dokument efter plats 20 kan användas direkt | Implementerat | Exakt `loadCopilotReferencedChoices` i [klient-API][cop-client]; [19 riktade regressioner][source-tests]. | Centrala väljaren är fortfarande ett begränsat urval. Krypterade historiska händelser måste finnas i betrodd, aktuell kanonisk klientprojektion; annars nekas de. |
| AI10 | Granskad meddelandetext får inte bytas före överföring | Implementerat | Klientens UTF-8-SHA256 och serverns `expected_text_sha256` i [klient][cop-client]/[källtjänst][cop-service]; [riktiga TLS-regressioner][cop-tests]. | Digests binds av den aktuella klienten; äldre direkta HTTP-klienter som utelämnar det kompatibla valfria källfältet får ID-baserad serverupplösning. |
| AI11 | Citerade svar och behörighetskontrollerad källgranskning | Implementerat | `[S1]`-validering och kontroll efter nätverk i [API/tjänst][cop-api]; [webbläsarresa][cop-browser]. | Endast valda källor får citeras. Borttagen/ändrad källa kan göra gammal historik otillgänglig. Inline-svar använder den lokalt granskade bilden, inte en bortstädad konversations länk. |
| AI12 | Sammanfatta, nästa steg, svar och uppgiftsutkast med mindre skrivande | Implementerat | [Intent-instruktioner](../../frontend/src/lib/copilot-intents.ts), [tester](../../frontend/tests/copilot-intents.test.mjs), [UI][cop-ui]. | Förbereder en instruktion och bevarar befintlig text. Varken väljer extra källor, skickar, medger eller skriver automatiskt. |
| AI13 | AI direkt vid meddelande och kanalsamtal | Implementerat | [Chat](../../frontend/src/components/Chat.tsx#L954) → [ContextualCopilot][context-ui] → verkligt Copilot-API; [15-gruppers webbläsarresa][cop-browser]. | Kanalrecap väljer högst åtta aktuella läsbara meddelanden. Inte en full historiksammanfattning. |
| AI14 | AI för huvuddraft och tråddraft | Implementerat | [Chat](../../frontend/src/components/Chat.tsx#L954), exakta target/token/scope-vakter, [komponenttester][context-tests] och faktisk huvuddraft i [webbläsartest][cop-browser]. | Separat klick krävs för ersätt/append. Ändrad draft ogiltigförklarar svaret. Inget meddelande skickas av AI-hjälpen. |
| AI15 | AI direkt i delade dokument | Implementerat | [Dokumentpanel](../../frontend/src/components/SharedDocumentsPanel.tsx#L80) → samma [komponent][context-ui]; [editorvakter](../../frontend/tests/contextual-editor-wiring.test.mjs). | Betrodd Yjs-klient, granskad text/version, högst 12 000 källtecken. Explicit append är en verklig synkroniserad redigering. |
| AI16 | AI direkt i projekt- och uppgiftsbeskrivningar | Implementerat | [Projekt/uppgiftspanel](../../frontend/src/components/ProjectTasksPanel.tsx#L180), [editorvakter](../../frontend/tests/contextual-editor-wiring.test.mjs). | Granskade begränsade fält skickas i prompten utan falska native-citat. AI ändrar bara beskrivningsutkast; vanlig Save krävs. |
| AI17 | Privat AI-historik, reset och exportgränser | Implementerat | [Konversationsmodeller](../../backend/app/models/copilot.py), [API][cop-api], [privata exporter](../../backend/app/services/data_control.py), [tester][cop-tests]. | Serverläsbar native-historik krypteras i vila. Inline skapar en egen engångskonversation och städar den; misslyckad städning visas ärligt. Reset ångrar inte redan godkända delade uppgifter. |
| AI18 | Krypterat innehåll kan väljas med uttrycklig AI-varning | Implementerat | [Betrodd förberedelse][cop-client] → [secure relay][cop-service], [secure-tester][cop-tests]. | Vald klartext lämnar E2EE via serverrelay till leverantören. Ingen AI-nyckel eller modell gör extern inferens end-to-end-krypterad. Secure prompt/svar lagras inte som serverhistorik. |
| AI19 | Faktisk mottagare/modell, API-kostnader och retention före Send | Implementerat | [Centrala UI:t][cop-ui], [inline][context-ui], [ChannelApps][apps-ui]; [review-tester][app-review-tests]. | Visar konfigurerad hostname/modell, källantal och abonnemang/API-skillnad. Ingen faktisk monetär prisberäkning eller kostnadsgräns. |
| AI20 | AI får föreslå men inte själv skapa uppgifter | Implementerat | Fast `create_task` i [tjänst][cop-service] → separat [approve-endpoint][cop-api]; [idempotens- och destinationstester][cop-tests]. | Endast valt native-projekt och aktuella administrationsrättigheter. Secure-kanaler får inte verktyget. Inline anropar aldrig approve. |
| AI21 | Publik demo med samma produktkomponenter/API | Implementerat; privat produktdemo-proof 16/16 grupper godkända 2026-10-23 | [Produktdemo](../PRODUCT_DEMO.md), [riktig Chat](../../frontend/src/components/Chat.tsx), [sessionram](../../frontend/src/components/ProductDemoSession.tsx), [verklig browserproof](../../frontend/tests/product-demo-browser.py). | Isolerade tillfälliga native-exempeldata. Extern AI är avstängd i publik miljö; ingen separat templatehjälp eller fabricerad inferens. Samma produktkomponenter visar faktiskt konfigurationsläge. |
| AI22 | AI direkt vid riktiga dokumentstackar | Implementerat | [Native stackkort](../../frontend/src/components/SharedDocumentOrganization.tsx#L144) → [ContextualCopilot][context-ui] → exakt verifierade valda Yjs-sidor → riktig privat HTTPS/SSE; [verklig stack/provider-grupp][cop-browser]. | Uttryckligt val av högst åtta aktuella sidor för read-only sammanfattning, läsordning eller öppna frågor. 24k-preflight inkluderar källheaders. Omarkerade sidor, sparade meddelanden och grupperade kommentarer följer inte med. Inga påhittade stack-källtyper, automatisk omgruppering eller andra writes. |
| AI23 | AI i alla andra funktioner, exempelvis feed/beslut/filverktygsfält | Saknas | Aktuella faktiska inline-anropsställen är [Chat](../../frontend/src/components/Chat.tsx), [dokument](../../frontend/src/components/SharedDocumentsPanel.tsx), [stackar](../../frontend/src/components/SharedDocumentOrganization.tsx), [uppgifter](../../frontend/src/components/ProjectTasksPanel.tsx) och [Apps][apps-ui]. | Central källselektion för filer/klipp finns. Det är inte en kontextuell AI-integration i varje produktfunktion. |
| AI24 | Äldre anonym Groq-demo | Implementerat | Avvecklad i [routing](../../backend/app/main.py); [fresh-process-regression](../../backend/tests/test_retired_demo_ai.py). | Den gamla flaggan ignoreras, båda gamla POST-alias ger404, ingen Groq-klient skapas. Produktdemon återanvänder riktiga komponenter men har ingen extern AI-provider. |

## Installerade appar och godkänd AI-automation

| ID | Förmåga/beslut | Status | Konkret väg och verifiering | Begränsning |
| --- | --- | --- | --- | --- |
| AP01 | Installera kundens manifest med exakta scopes/kanaler | Implementerat | [Integrations-UI](../../frontend/src/components/AppsPanel.tsx) → [app-API][app-api] → `AppInstallation`/`AppRevision`; [tester][app-tests]. | Personlig arbetsyte- och kanaladministratör; endast native arbetsytekanaler. Sparad installation betyder inte lyckad leverantörstest. |
| AP02 | Avgränsade bot-identiteter | Implementerat | [Modell](../../backend/app/models/app_platform.py), [installationsservice][app-service], [scope-tester][app-tests]. | Bot-ID/signing secret är inte användarkonto, admin-JWT eller historikläsande bot. |
| AP03 | Signerad custom app/n8n-mottagare | Implementerat | Fast `app.command`-POST, [referensmottagare](../../examples/apps/signed_receiver.py), [verklig mottagare i browser][app-browser]. | At-least-once: mottagaren måste deduplicera stabilt event-ID. Inget godtyckligt skriptverktyg. |
| AP04 | GitHub issue-recept | Implementerat | `github.issue` i [katalog/protokoll][app-service]; parametriserat faktiskt HTTP-fixturtest i [tester][app-tests]. | Explicit skapa issue i valt repo. Ingen komplett GitHub- eller PR-synk. |
| AP05 | Jira Cloud-recept | Implementerat | `jira.issue` i [protokoll][app-service]; [tester][app-tests]. | Skapa Task med rätt projekt/ADF. Kunden hanterar token/gateway och rättigheter. |
| AP06 | Zendesk-recept | Implementerat | `zendesk.ticket` i [protokoll][app-service]; [tester][app-tests]. | Skapa ticket. Ingen egen Zendesk OAuth-refresh eller generell synk. |
| AP07 | Salesforce-recept | Implementerat | `salesforce.case` i [protokoll][app-service]; [tester][app-tests]. | Skapa Case via kundens instans/Connected App-behörighet. |
| AP08 | Google Calendar- och Microsoft Calendar-appåtgärder | Implementerat | [App-protokoll][app-service] + [personlig OAuth](../../backend/app/services/personal_oauth.py); [OAuth-tester](../../backend/tests/test_personal_oauth.py). | Separat egen användardelegering för kalenderwrite; inga implicit lånade admin-konton, inga attendees i detta recept. |
| AP09 | Skarp acceptans hos de externa app-leverantörerna | Externt overifierat | [Appdokumentation](../APP_PLATFORM.md#seven-working-setup-recipes) och privata verkliga protokollfixturer. | Kundens registreringar, tokenlivscykel, scopes, policyer och fjärrresultat måste verifieras i kundmiljön. |
| AP10 | Medlem kan köra exakt granskat appkommando | Implementerat | [ChannelApps][apps-ui] → `Invocation(confirm,disclose_to_app)` → [outbox][app-service]; [browser][app-browser]. | Kommandot väljs i formulär. All kanalhistorik inkluderas inte. |
| AP11 | Slack-lik slash-kommandotolkning i vanliga skrivfältet | Saknas | Installerade kommandon nås via [Apps-formuläret][apps-ui]; ordinarie Chat saknar spårad parser→manifestgranskning→invoke-väg. | Etiketten ”Slash command” i en select betyder inte att `/kommando` i chatten exekveras. |
| AP12 | Beständig leverans, retry och ärliga osäkra resultat | Implementerat | `AppJob` → [leased worker][app-service] → [integration worker][worker]; [riktiga mottagar-/retrytester][app-tests]. | Högst åtta automatiska försök; tvetydiga provider-create-resultat blir `uncertain`, kräver manuell kontroll. HTTP-acceptans är inte färdig fjärrprocess. |
| AP13 | Signerade callbacks och ofarliga botkort | Implementerat | [Callback-API][app-api] → `AppCallback`/`AppCard` → [UI][apps-ui]; [replay/generation-test][app-tests]. | HMAC över exakt body, fem minuter, max16KiB, aktuella grants/generation. Text/fält/länk; inga exekverbara kort eller unfurl-hämtningar. |
| AP14 | Ändra/revokera installation utan gamla behörigheter | Implementerat | [API][app-api]/[service][app-service]; generation/key-rotation och köannullering; [tester][app-tests]. | Ny version granskar om manifest, kanaler, destination och credentials. Extern redan utförd effekt kan inte tas tillbaka. |
| AP15 | Citerad privat AI-sammanfattning | Implementerat | [ChannelApps][apps-ui] → strikt [AIInput/generate_draft][app-service] → krypterad `AppAIDraft`; [TLS-test][app-tests]/[browser][app-browser]. | Högst åtta valda native-källor/24k; full message-review, granskad configrevision och texthash. Native/DM/E2EE-gränser gäller. |
| AP16 | Separat publicering av granskat AI-utkast | Implementerat | [Approve-endpoint][app-api] → citerat `AppCard`; [faktisk privat→publicerad resa][app-browser]. | Generering publicerar inte. Källor, medgivande och grants kontrolleras igen. |
| AP17 | AI-genererade arbetsflöden | Implementerat | [Generering/schema][app-service] → [separat install/reauth][app-api]; [atomiskt idempotent test][app-tests]. | Bara manuella notify/approval-flöden till uttryckligen valda nuvarande medlemmar. Inte fritt genererade triggers, skript eller native task-fält. |
| AP18 | AI föreslår flera appåtgärder | Implementerat | Valda manifest/schema → [1–3 validerade förslag][app-service] → [separat approval/outbox][app-api]; [tester][app-tests]. | Bara valda oförändrade installationer/kommandon/argument. AI ser inga credentials och väljer ingen ny URL. |
| AP19 | Återkommande privata recaps | Implementerat | [Granskad schemaläggning][apps-ui] → [API][app-api] → `ChannelRecapSchedule` → [worker][worker]/[run_recaps][app-service]; [test][app-tests]. | 1–168h; första kontroll efter ett intervall. Högst åtta nya native-meddelanden efter senaste kontroll/uttryckliga grant. Ingen äldre historik före medgivande/reenable/providerbyte. Privata utkast, ingen autopublicering. Fel/återkallelse pausar; stop under körning kasserar resultat. |
| AP20 | Privat städning, exports och retention | Implementerat | [Appkonto-/draft-livscykel][app-service] och [datakontroller](../../backend/app/services/data_control.py); [privacy/retention-tester][app-tests]. | Egna privata drafts; holds kan hindra borttagning. Delade kort/attribution och content-free replay-tombstones har andra livscykler än privata payloads. |

## Deterministiska arbetsflöden

| ID | Förmåga/beslut | Status | Konkret väg och verifiering | Begränsning |
| --- | --- | --- | --- | --- |
| WF01 | Visuell builder med konfiguration/versioner | Implementerat | [WorkflowsPanel][wf-ui] → [API][wf-api] → `WorkflowDefinition`/`WorkflowRevision`; [tester][wf-tests]. | Högst20 definitioner/kanal och fem steg. Save/edit/pause/cancel kräver aktuell personlig workspace+channel-admin och lösenord/MFA. |
| WF02 | Manuellt startat valt flöde | Implementerat | [UI][wf-ui] → `/{workflow}/run` → [kö][wf-service]; [selektivt starttest][wf-tests]. | Inte alla manuella definitioner samtidigt; aktuellt kanalmedlemskap krävs. |
| WF03 | Filuppladdning och filversion som event | Implementerat | [Transaktionsbundet producer-hook](../../backend/app/services/webhooks.py#L172) → [kö][wf-service]; [riktig browser](../../frontend/tests/workflows-browser.py). | Native fil-events; Matrix media ger inte native filtrigger. |
| WF04 | Nytt delat dokument som event | Implementerat | [Dokument-API](../../backend/app/api/shared_documents.py) → [workflow-kö][wf-service]; [tester][wf-tests]. | Skapandeevent, inte godtycklig läsning/redigeringsanalys av dokument. |
| WF05 | Beslutsrevision som event, inklusive statusvillkor | Implementerat | [Decision create/revise](../../backend/app/api/decisions.py#L123) → lokal bounded status → [kö][wf-service]; ny faktisk API→worker-regression i [tester][wf-tests]. | `proposed`/`accepted`/`superseded` bara native. Rättat i denna granskning: statusfält accepterades tidigare men producenten skickade det inte. |
| WF06 | Uppgiftsevent och statusvillkor | Implementerat | [Native task-service](../../backend/app/services/project_tasks.py) → [kö][wf-service]; [tester][wf-tests]. | `todo`/`doing`/`done`; workflow-skapade tasks utlöser inte en ny workflow-loop. |
| WF07 | Avsändarvillkor | Implementerat | `Conditions.actor` → faktisk event-author i [kö][wf-service]; [metadata-/creator-tester][wf-tests]. | Any current member eller den admin som sparade revisionen; ingen fri innehålls-/kodregel. |
| WF08 | Godkännande, quorum och oföränderliga val | Implementerat | [Vote-API][wf-api] → `WorkflowVote` → [dispatch][wf-service]; [tester][wf-tests]. | Ett val/person/steg; aktuell medlemskap/aktivt konto krävs. Omöjligt quorum annullerar. Rejection stoppar senare steg. |
| WF09 | Deadline, påminnelser och eskalering | Implementerat | [Builder][wf-ui] → [worker-tillstånd][wf-service]; [restart/reminder/escalation-tester][wf-tests]. | 0–3 reminders och begränsade tidsvärden. Eskalerade personer blir inte godkännare före eskalering. |
| WF10 | In-app-notifiering och native task action | Implementerat | [Fast stegdispatch][wf-service] → `Notification`/native idempotent task-helper; [tester][wf-tests]. | Inga godtyckliga HTTP-/e-post-/skriptsteg. Task bara native befintligt projekt. |
| WF11 | Beständiga körningar, leases och återhämtning | Implementerat | `WorkflowRun`/`WorkflowActivity` → [run_pending][wf-service] → [worker][worker]; [claim/retry-tester][wf-tests]. | 60s leases, högst fem återförsök och begränsade batcher. Separat worker måste köras; inte löfte om godtycklig flernods-kapacitet. |
| WF12 | Körhistorik, avbryt/pausa och revidera | Implementerat | [UI][wf-ui] → [API][wf-api]; [revision/cancel-tester][wf-tests]. | Senaste50 visas normalt, API max100/paging; max2000 retained runs. Full aktiv kapacitet ger429 atomiskt. Avbryt ångrar inte färdiga effekter. |
| WF13 | Krypterade godkännanden utan serverklartext | Implementerat | [Betrodd vote-klient](../../frontend/src/lib/workflows-api.ts) → opaque eventref → [API/worker][wf-api]; [secure-tester][wf-tests]. | Generiska notiser/metadata; optional note dekrypteras och verifieras på klienten. Inga statusfilter eller server-task actions i secure-kanaler. |
| WF14 | Godtycklig extern eller autonom automation | Saknas | Det fasta [stegschemat][wf-service] stöder endast approval/notify/create_task. | Appar har en separat granskad outbox. Dessa domänfunktioner utgör inte en generell kod-/agentplattform. |

## Orkestrering och kvarvarande AI-löften

Det bekräftade produktönskemålet om naturlig AI i alla funktioner är ännu inte
fullt uppfyllt: AI23 beskriver konkreta kvarvarande AI-luckor; AI22 har nu en riktig
nåbar stackintegration med granskade valda sidor och read-only svar. Automatiska
struktur-/relationsförslag behöver inte en modell; verklig regelbaserad native
hjälp ska räknas i dokumentgranskningen och inte felaktigt förklaras saknad här.
AP11 hör till önskemålet om full Slack-paritet. OR02–OR06
är föreslagna arkitektursteg i orkestreringsdokumentet, inte separata redan
levererade milstolpar. OR07:s personliga nycklar/native-adaptrar var inte ett
bekräftat implementationsbeslut. OR08 redovisar jämförelsens ännu ej byggda
möjligheter; det fasta säkra verktygskontraktet utlovar inte godtycklig exekvering.

| ID | Förmåga/beslut | Status | Belägg och konkret lucka |
| --- | --- | --- | --- |
| OR01 | Inbyggd kontroll av en AI-förfrågan, kontext, provider, verktyg och approval | Implementerat | [Copilot][cop-api]/[tjänst][cop-service] och [app-drafts][app-service] gör riktig policykontrollerad körning. Det är korrekt att en begränsad inbyggd orkestrering redan finns. |
| OR02 | Gemensam run/step-modell för alla AI-flöden | Endast förberett | [Arkitekturdokumentet](../AI_ORCHESTRATION.md#ordered-next-slice) beskriver nästa leverans. `CopilotTurn`, `AppAIDraft`, recap-schedule och `WorkflowRun` är fortfarande olika modeller. Ingen gemensam nåbar run-API/timeline. |
| OR03 | Varaktig generell AI-jobbkörning, cancel/resume och worker-recovery | Saknas | Vanliga Copilot-completions körs synkront i requesten; gamla processing-turns blir failed. Recap-claims är verkliga men ersätter inte en generell AI-jobbmotor. |
| OR04 | Modeller per agent/profil, modellrouting och flera leverantörer samtidigt | Saknas | [Provider/Agent-modeller][cop-service] innehåller en endpoint/modell per arbetsyta. Ingen profillista/router; preset-val är konfigurationshjälp. |
| OR05 | Begränsad flerstegsplanering/agenthandoff | Saknas | Fast verktygsvalidering och godkända 1–3 appförslag finns; ingen planner som kedjar modellsteg, utför routing och redovisar en gemensam körning. |
| OR06 | Token-/kostnadsredovisning och verklig spending budget | Saknas | Transportstorlek/deadline/tokenbegäran och rate limits finns. Ingen provider-usage-ledger eller monetär arbetsytebudget. Varning om avgifter uppfyller inte detta. |
| OR07 | Personliga AI-nycklar och native Anthropic/Azure/Gemini/private Ollama/vLLM | Saknas | Nuvarande [providerkontrakt](../COPILOT.md#configure-a-provider-and-agents) är kompatibel publik HTTPS443 per arbetsyta. En kompatibel gateway kan passa kontraktet, men är inte en implementerad native-adapter eller privat nätanslutning. Personliga nycklar var inte ett tidigare bekräftat implementationsbeslut. |
| OR08 | AI-webbsökning och fri behörighetsstyrd kunskapsinhämtning | Saknas | `context_sources` listar bara redan valda citat-ID. Varken webbsökverktyg, workspace-RAG eller godtyckliga konfigurerade HTTP-verktyg finns. Weavys jämförelserad måste därför inte läsas som levererad motsvarighet. |

## Rättningar och evidens från denna granskning

1. Apps & automation krävde tidigare inte granskad providerrevision. Både omedelbara drafts och schemaskapande kräver nu strikt positiv `expected_config_revision`; stale review nekas före networking även efter medgivande till ny revision i annan flik. Recap-worker behåller sin egen aktuella grant/revision-kontroll.
2. ChannelApps skickade tidigare bara meddelande-ID efter en 260-teckens preview. Full källtext kan nu granskas, urvalet vägras om det är för stort och exakt native-text binds med SHA256. UI:t visar faktiska API-/retentionsvarningar. Ändrade appdestinationer, mottagare och intervall återställer relevant disclosure-val.
3. Native decision-status accepterades i workflow-schemat men nådde inte producenten. Validerad status når nu endast lokal workflow-kö, med motsvarande val i UI och riktig decision API→worker→notification-regression. Externa webhook-payloads och E2EE-gränsen utökas inte.
4. Den oanvända anonyma Groq-demonstrationen avvecklades. En separat process med historisk flagga satt till true och en syntetisk nyckel bevisar404 för båda alias och noll provider-konstruktioner. Produktions-AI är kvar.
5. App-draft-serverns 24k-gräns räknar nu även avskiljarna mellan källorna, i linje med det faktiskt överförda innehållet. Ett direkt anrop på fel sida av gränsen ger413 utan provider-I/O.
6. Recap-medgivandet gällde framtida meddelanden men första worker-körningen kunde tidigare skicka historik från före medgivandet. Varje enable/regrant sätter nu en ny disclosure-cursor; första kontroll sker efter ett intervall. Pausad historik och äldre providerkontext följer inte med efter reenable. Legacy-rader utan cursor får ingen historik-backfill. `last_run_at` dokumenteras som disclosure-/kontrollcursor, inte som en utförd lyckad AI-körning.
7. Native stackar har nu riktig AI-hjälp från explicit valda aktuella dokument. Den gemensamma inline-komponenten räknar faktisk kontext med Unicode-tecken, citat-/kindheaders och källavskiljare före Send. En enda karaktär över 24k ger guidning och noll anrop; exakt 24k med emoji skickas intakt enligt regressionstesterna.

Körda kontroller: **27 backendtester godkända** för den slutliga appplattformen/recap-rättningen och avvecklad demo; **66 backendtester** för arbetsflöden och webhooks har också passerat. Det är **93 unika backendfall**, inklusive faktisk TLS, pre-consent/paused/provider-regrant-gränser och legacy-scheman utan cursor. **5 nya frontendtester** verifierar full källgranskning, payloadbindning, actorbyte, recap-medgivande och urvalsgränser; **4 i18n-tester** och TypeScript är godkända. Den utökade [verkliga app-browser-resan][app-browser] passerade **9/9 grupper**, bevarade alla ursprungliga receiver-/callback-kontroller och tillförde privat HTTPS/SSE-AI, faktisk revision/digest, separat publicering, stale schedule-granskning och verklig future-only recap-worker utan autopublicering. Slutrapportens tio källhashar var oförändrade under körningen.

Kompletterande slutkontroll för native stack-AI: **13 komponenttester + 4 i18n-tester
godkända**. [Copilot-webbläsarresan][cop-browser] passerade **15/15 grupper**, med
**26 aktuella källhashar** efter de slutliga chatt-, kanalmetadata- och socketfixarna. Den nya gruppen
skapar riktiga Yjs-sidor, en kommentar och en native stack, avmarkerar en sida och
skickar endast den kvarvarande verifierade sidans text till privat TLS-provider.
Omarkerad sida, kommentar och konversationsmeddelanden följer inte med; stack,
placeringar, dokumentuppdateringar, tasks och meddelanden förblir oförändrade.

Privata bevis och loggar sparas under `/workspace/.freecollab/proof-results/feature-audit-20261023/`. Testkonton, nycklar, TLS-CA, SQLite, mottagare och processer är engångsfixturer. Inga riktiga leverantörsanrop eller hemliga kunduppgifter har använts. Saknade förmågor ovan är fortfarande saknade; dokumentationen gör dem inte till en senare ”redan levererad” milstolpe.

[cop-ui]: ../../frontend/src/components/CopilotPanel.tsx
[cop-api]: ../../backend/app/api/copilot.py#L147
[cop-service]: ../../backend/app/services/copilot.py#L48
[cop-client]: ../../frontend/src/lib/copilot-api.ts
[cop-tests]: ../../backend/tests/test_copilot.py
[cop-browser]: ../../frontend/tests/copilot-browser.py
[source-tests]: ../../frontend/tests/copilot-referenced-sources.test.mjs
[presets]: ../../frontend/src/lib/connection-presets.ts#L23
[preset-tests]: ../../frontend/tests/connection-presets.test.mjs
[context-ui]: ../../frontend/src/components/ContextualCopilot.tsx
[context-tests]: ../../frontend/tests/contextual-copilot.test.mjs
[apps-ui]: ../../frontend/src/components/ChannelApps.tsx
[app-api]: ../../backend/app/api/app_platform.py
[app-service]: ../../backend/app/services/app_platform.py
[app-tests]: ../../backend/tests/test_app_platform.py
[app-browser]: ../../frontend/tests/app-platform-browser.py
[app-review-tests]: ../../frontend/tests/app-ai-review.test.mjs
[worker]: ../../backend/app/services/integration_worker.py#L22
[wf-ui]: ../../frontend/src/components/WorkflowsPanel.tsx
[wf-api]: ../../backend/app/api/workflows.py
[wf-service]: ../../backend/app/services/workflow.py
[wf-tests]: ../../backend/tests/test_workflows.py
