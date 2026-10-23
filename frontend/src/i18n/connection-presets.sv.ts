const catalog: Record<string, string> = {
  "Google files": "Google-filer",
  "Google calendar": "Google-kalender",
  "Microsoft files": "Microsoft-filer",
  "Microsoft calendar": "Microsoft-kalender",
  "Nextcloud files": "Nextcloud-filer",
  "Nextcloud calendar": "Nextcloud-kalender",
  "CalDAV calendar": "CalDAV-kalender",
  "This recipe fills safe defaults only. Supply your own provider application and selected resource. Nothing is saved, authorized or tested automatically.": "Guiden fyller bara i säkra standardvärden. Ange din egen leverantörsapplikation och vald resurs. Inget sparas, godkänns eller testas automatiskt.",
  "Required: your OAuth client ID and the selected folder or calendar ID. Microsoft also requires your directory tenant ID and, for files, drive ID. Add the app secret if your client requires one.": "Obligatoriskt: ditt OAuth-klient-ID och ID för vald mapp eller kalender. Microsoft kräver även katalogens klientorganisations-ID och, för filer, enhetens ID. Lägg till apphemligheten om din klient kräver den.",
  "Required: your exact collection URL, provider username and an app-specific password. The example address is a hint, not a saved destination.": "Obligatoriskt: samlingens exakta URL, leverantörens användarnamn och ett appspecifikt lösenord. Exempeladressen är en ledtråd och sparas inte som mottagare.",
  "Google calendar defaults to primary. Replace it with a specific calendar ID if needed before consenting.": "Google-kalendern använder primary som standard. Byt vid behov till ett specifikt kalender-ID innan du godkänner.",
  "Users opt in separately after the workspace provider is saved. This setup does not send prompts or selected content.": "Användare godkänner var för sig efter att arbetsytans leverantör har sparats. Denna konfiguration skickar inga frågor eller valt innehåll.",
  "AI provider preset": "Förval för AI-leverantör",
  "OpenAI · GPT-4o mini": "OpenAI · GPT-4o mini",
  "OpenAI · GPT-4.1 mini": "OpenAI · GPT-4.1 mini",
  "Grok · xAI": "Grok · xAI",
  "Your OpenAI-compatible service": "Din OpenAI-kompatibla tjänst",
  "Presets fill editable fields only. Nothing is saved or sent until you explicitly save and later choose to use AI.": "Förval fyller bara i redigerbara fält. Inget sparas eller skickas förrän du uttryckligen sparar och senare väljer att använda AI.",
  "Required: your provider endpoint, model ID, API key and current FreeCollab password. Add an authenticator or recovery code if your account uses MFA.": "Obligatoriskt: leverantörens ändpunkt, modell-ID, API-nyckel och ditt nuvarande FreeCollab-lösenord. Lägg till en autentiserings- eller återställningskod om ditt konto använder MFA.",
  "Your provider API key": "Din leverantörs API-nyckel",
  "Changing the endpoint clears any typed API key and confirmation credentials. A different endpoint requires a new provider key.": "När ändpunkten ändras rensas inmatad API-nyckel och uppgifter för bekräftelse. En annan ändpunkt kräver en ny leverantörsnyckel."
};
export default catalog;
