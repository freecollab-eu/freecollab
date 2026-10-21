import { useEffect } from 'react';
import type { CollaborationClient } from '@freecollab/components';
import { Chat, Files, Feed, Notifications } from '@freecollab/components/react';
export function Collaboration({ client, userRevision }: { client: CollaborationClient; userRevision: string }) {
  useEffect(() => { void client.reauthenticate().catch(() => {}); }, [client, userRevision]);
  return <><Chat client={client} locale="en" /><Files client={client} /><Feed client={client} /><Notifications client={client} /></>;
}
// Your host session service creates this shared client and calls client.destroy()
// when the application session closes. Each wrapper disposes its own view on
// unmount and supports React StrictMode's setup/cleanup replay.
