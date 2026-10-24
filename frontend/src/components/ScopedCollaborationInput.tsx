import { forwardRef } from 'react';
import { CollaborationInput, type CollaborationInputElement, type CollaborationInputProps } from './CollaborationInput';
import { useCollaborationContext } from '../lib/collaboration-context';

/** Other channel editors cannot inherit the active conversation's reference authority. */
export const ScopedCollaborationInput = forwardRef<CollaborationInputElement, CollaborationInputProps & { channelId: string }>(function ScopedCollaborationInput({ channelId, allowCommands = true, allowReferences = true, ...props }, ref) {
  const context = useCollaborationContext(), matches = !!context && context.channelId === channelId;
  return <CollaborationInput {...props} ref={ref} allowCommands={matches && allowCommands} allowReferences={matches && allowReferences} />;
});
