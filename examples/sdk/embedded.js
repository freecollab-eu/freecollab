import {mountWorkspace} from '@freecollab/components/embed';
import {createEmbedSignIn} from '@freecollab/components/auth';

// Public deployment configuration, never a token or customer client secret.
const appUrl = 'https://collaboration.your-company.eu';
const applicationId = 'replace-with-registered-embed-application';
const workspaceId = 'replace-with-workspace-id';
const channelId = 'replace-with-channel-id';
let workspace = null;
let signIn = null;
const status = document.querySelector('#status');

document.querySelector('#connect').addEventListener('click', () => {
  workspace?.destroy(); signIn?.destroy();
  try {
    signIn = createEmbedSignIn({appUrl, applicationId, locale: 'sv'});
    signIn.open(); // Must run synchronously in the user's click before any await.
    workspace = mountWorkspace(document.querySelector('#workspace'), {
      appUrl, applicationId, workspaceId, channelId,
      locale: 'sv', module: 'workspace',
      grantFactory: signIn.grantFactory,
      theme: {accent: '#355d48', background: '#f4f3ee', surface: '#ffffff', text: '#263a32', font: 'system-ui'},
      media: document.querySelector('#media').checked,
      onState: ({status: state}) => {status.textContent = ({connecting: 'Ansluter…', ready: 'Ansluten', 'signed-out': 'Utloggad', error: 'Anslutningen kunde inte slutföras.', destroyed: 'Frånkopplad'})[state];},
      onError: () => {status.textContent = 'Anslutningen kunde inte slutföras. Kontrollera organisationens inställningar och försök igen.';},
    });
    void workspace.ready.catch(() => {});
  } catch {
    signIn?.destroy();
    status.textContent = 'Öppna inloggningsfönstret från knappen och kontrollera era anslutningsinställningar.';
  }
});
document.querySelector('#disconnect').addEventListener('click', () => {
  workspace?.destroy(); signIn?.destroy(); workspace = signIn = null;
  status.textContent = 'Frånkopplad';
});
window.addEventListener('pagehide', () => {workspace?.destroy(); signIn?.destroy();}, {once: true});
// On your host's account change or logout, destroy the old workspace immediately.
// A new user explicitly signs in again. No decrypted content is sent to this page.
