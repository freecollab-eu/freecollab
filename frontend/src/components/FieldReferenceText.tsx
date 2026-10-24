import { useCollaborationContext } from '../lib/collaboration-context';
import type { MessageReference } from '../lib/message-references';
import { ReferencedMessage } from './ReferencedMessage';

/** Resource fields use the same fresh, permission-checked reference navigation. */
export function FieldReferenceText({ value, references, channelId }: { value: string; references?: MessageReference[]; channelId: string }) {
  const collaboration = useCollaborationContext();
  if (!collaboration?.onOpenReference || collaboration.channelId !== channelId || !references?.length) return <>{value}</>;
  return <ReferencedMessage content={value} references={references} channelId={channelId} workspaceId={collaboration.workspaceId}
    secure={!!collaboration.secure} onOpen={(reference, lease) => collaboration.onOpenReference?.(reference, undefined, lease.isCurrent, lease.element)} />;
}
