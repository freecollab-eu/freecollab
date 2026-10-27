import { ArrowUpLeft, MessageSquare } from 'lucide-react';
import type { Message } from '../lib/api';
import { conversationSourcePreview } from '../lib/conversation-source-ribbon';
import { t, useI18n } from '../i18n';
import '../styles/conversation-source-ribbon.css';

interface Props {
  sourceMessage: Message;
  channelId: string;
  channelName?: string;
  authorName?: string;
  isCurrent: () => boolean;
  onBeforeReveal?: () => void;
  onRevealSource?: () => void;
}

/** Returning changes the view through the host; it never closes the editor. */
export function ConversationSourceRibbon({ sourceMessage, channelId, channelName, authorName, isCurrent, onBeforeReveal, onRevealSource }: Props) {
  const { locale } = useI18n();
  const source = conversationSourcePreview(sourceMessage, channelId);
  if (!source || !isCurrent()) return null;
  const author = authorName?.trim();
  const when = source.createdAt ? new Intl.DateTimeFormat(locale, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(new Date(source.createdAt)) : null;
  return <section className="fc-conversation-source-ribbon" aria-label={t('Source conversation')}>
    <MessageSquare size={18} aria-hidden="true" />
    <div className="fc-source-ribbon-context">
      <div className="fc-source-ribbon-meta">
        <span className="fc-source-ribbon-label">{t('From this conversation')}</span>
        <strong>{channelName || t('Your channel')}</strong>
        {author && <span>{author}</span>}
        {when && <time dateTime={source.createdAt!}>{when}</time>}
      </div>
      {source.quote && <p>{source.quote}</p>}
    </div>
    {onRevealSource && <button type="button" className="fc-source-ribbon-return" onPointerDown={() => { if (isCurrent()) onBeforeReveal?.(); }} onClick={() => { if (isCurrent()) onRevealSource(); }}>
      <ArrowUpLeft size={17} aria-hidden="true" /><span>{t('Back to source message')}</span>
    </button>}
  </section>;
}
