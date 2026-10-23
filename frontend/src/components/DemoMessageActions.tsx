import { ArrowUpRight, CheckSquare } from 'lucide-react';
import { demoChannels, type DemoChannel, type DemoMessage } from '../lib/demo-showroom';
import '../styles/demo-message-actions.css';

export interface DemoMessageActionsProps {
  message: DemoMessage;
  channel: DemoChannel;
  t: (source: string, values?: Record<string, string | number>) => string;
  onMakeTask: (message: DemoMessage, source: HTMLElement) => void;
  existingTask?: boolean;
  disabledTask?: boolean;
}

/** Reuse the sent message; the parent opens the source-aware task form. */
export function DemoMessageActions({ message, channel, t, onMakeTask, existingTask = false, disabledTask = false }: DemoMessageActionsProps) {
  if (!demoChannels.includes(channel) || !message.id) return null;
  const Icon = existingTask ? ArrowUpRight : CheckSquare;
  return <button type="button" className="fc-demo-message-task" disabled={disabledTask && !existingTask} draggable={false}
    aria-label={t(existingTask ? 'View task for this message' : 'Make a task from this message')}
    data-message-task-source={message.id} data-message-channel={channel}
    onClick={event => onMakeTask(message, event.currentTarget)}>
    <Icon size={13} aria-hidden="true" /><span>{t(existingTask ? 'View task' : 'Make a task')}</span>
  </button>;
}
