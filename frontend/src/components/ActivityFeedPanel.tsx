import { ReviewedResourceCopilot } from './ReviewedResourceCopilot';
import { useResourceTargetFocus } from '../lib/useResourceTargetFocus';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Archive, ArrowDown, Check, Heart, History, MessageSquare, Pencil, Plus, RefreshCw, Rss, ThumbsUp, Trash2, UserPlus, X } from 'lucide-react';
import { useI18n } from '../i18n';
import { api, type ChannelMember } from '../lib/api';
import { activityFeedApi, type FeedComment, type FeedDetail, type FeedPost, type FeedPostFields, type FeedRecord, type FeedCommentFields } from '../lib/activity-feed-api';
import { CollaborationInput } from './CollaborationInput';
import { FieldReferenceText } from './FieldReferenceText';
import { feedReferenceTexts, reconcileFieldReferences } from '../lib/field-references';
import type { MessageReference } from '../lib/message-references';
import { EnterpriseAlerts } from './EnterpriseControls';
import '../styles/activity-feed.css';

interface Props { instanceId?: string; workspaceId: string; channelId: string; isAdmin?: boolean; onPendingChange?: (pending: boolean) => void; focusPostId?: string }
const blank = (): FeedPostFields => ({ title: '', body: '', poll: null });
const message = (error: unknown) => error instanceof Error ? error.message : 'The activity could not be completed.';

/** Deliberately small rich-text syntax. All user content remains React text. */
function SafeBody({ value, references = [], channelId }: { value: string; references?: MessageReference[]; channelId: string }) {
  const slice = (start: number, end: number) => <FieldReferenceText value={value.slice(start, end)} channelId={channelId} references={references.filter(ref => ref.start >= start && ref.end <= end).map(ref => ({ ...ref, start: ref.start - start, end: ref.end - start }))} />;
  const inline = (text: string, offset: number) => {
    let position = offset;
    const parts = text.split(/(\*\*[^*\n]+\*\*|`[^`\n]+`|\*[^*\n]+\*)/g).map(part => {
      const start = position; position += part.length;
      const trim = part.startsWith('**') && part.endsWith('**') ? 2 : ((part.startsWith('`') && part.endsWith('`')) || (part.startsWith('*') && part.endsWith('*'))) ? 1 : 0;
      return { part, start, end: position, trim };
    });
    // Resource names may themselves contain formatting characters. Preserve their whole binding.
    if (references.some(ref => ref.start >= offset && ref.end <= offset + text.length && !parts.some(part => ref.start >= part.start + part.trim && ref.end <= part.end - part.trim))) return slice(offset, offset + text.length);
    return parts.map(({ part, start, end, trim }, index) => trim === 2 ? <strong key={index}>{slice(start + 2, end - 2)}</strong>
      : trim === 1 && part.startsWith('`') ? <code key={index}>{slice(start + 1, end - 1)}</code>
      : trim === 1 ? <em key={index}>{slice(start + 1, end - 1)}</em> : <span key={index}>{slice(start, end)}</span>);
  };
  let offset = 0;
  return <div className="fc-feed-body">{value.split('\n').map((line, index) => {
    const start = offset; offset += line.length + 1;
    return <p className={line.startsWith('- ') ? 'fc-feed-bullet' : ''} key={index}>{line.startsWith('- ') ? <><span aria-hidden="true">• </span>{inline(line.slice(2), start + 2)}</> : line ? inline(line, start) : <br />}</p>;
  })}</div>;
}

export function ActivityFeedPanel({ instanceId, workspaceId, channelId, isAdmin = false, onPendingChange, focusPostId }: Props) {
  const { t, locale } = useI18n();
  const fieldId = (name: string) => instanceId ? instanceId + "-" + name : name;
  const fieldContext = (name: string) => `${instanceId || "primary"}:${channelId}:feed:${editor?.previous?.id || "new"}:${name}`;
  const [posts, setPosts] = useState<FeedPost[]>([]), [members, setMembers] = useState<ChannelMember[]>([]), [following, setFollowing] = useState<string[]>([]);
  const [filter, setFilter] = useState<'all' | 'following' | 'archived'>('all'), [query, setQuery] = useState(''), [next, setNext] = useState<string | null>(null);
  const [details, setDetails] = useState<FeedDetail | null>(null), [selected, setSelected] = useState<string[]>([]);
  const [editor, setEditor] = useState<{ fields: FeedPostFields; previous?: FeedPost } | null>(null);
  const [commentReferences, setCommentReferences] = useState<MessageReference[]>([]);
  const [comment, setComment] = useState(''), [commentEdit, setCommentEdit] = useState<FeedComment | null>(null);
  const [history, setHistory] = useState<FeedRecord<FeedPostFields | FeedCommentFields>[] | null>(null);
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(''), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const token = useRef(api.getAccessToken()), epoch = useRef(0), mounted = useRef(true), serial = useRef(0), detailId = useRef('');
  const foreground = useRef(false);
  const busyRef = useRef(''), editorRef = useRef(false); busyRef.current = busy; editorRef.current = !!editor;
  const current = (generation: number) => mounted.current && generation === epoch.current && token.current === api.getAccessToken();
  const actor = api.getCurrentUserId(), names = new Map(members.map(member => [member.user_id, member.name]));
  const name = (user: string) => names.get(user) || t('Former channel member');
  const when = (value: string) => new Date(/(?:Z|[+-]\d{2}:\d{2})$/.test(value) ? value : value + 'Z').toLocaleString(locale, { dateStyle: 'medium', timeStyle: 'short' });
  useEffect(() => { onPendingChange?.(!!editor || !!commentEdit || !!comment.trim() || !!busy); }, [editor, commentEdit, comment, busy, onPendingChange]);
  useEffect(() => () => onPendingChange?.(false), [onPendingChange]);

  async function load(more = false, quiet = false) {
    if (quiet && foreground.current) return;
    const generation = epoch.current, request = ++serial.current;
    if (!quiet) { foreground.current = true; setLoading(true); setError(''); }
    try {
      const [page, people] = await Promise.all([activityFeedApi.posts(channelId, { following: filter === 'following', archived: filter === 'archived', beforeId: more ? next || undefined : undefined }), api.getChannelMembers(channelId)]);
      if (!current(generation) || request !== serial.current) return;
      setPosts(previous => more ? [...previous, ...page.items.filter(item => !previous.some(old => old.id === item.id))] : page.items);
      setNext(page.next_before_id); setFollowing(page.following); setMembers(people);
      if (detailId.current && !more) {
        const detail = await activityFeedApi.detail(channelId, detailId.current);
        if (current(generation) && request === serial.current) setDetails(detail);
      }
    } catch (failure) { if (current(generation) && request === serial.current && !quiet) setError(t(message(failure))); }
    finally { if (current(generation) && request === serial.current && !quiet) { foreground.current = false; setLoading(false); } }
  }
  useEffect(() => {
    mounted.current = true; token.current = api.getAccessToken(); ++epoch.current; detailId.current = ''; foreground.current = false;
    setPosts([]); setDetails(null); setEditor(null); setHistory(null); setComment(''); setCommentReferences([]); setCommentEdit(null); setFollowing([]); setMembers([]); setQuery(''); setNext(null); setBusy('');
    void load();
    const timer = window.setInterval(() => { if (!busyRef.current && !editorRef.current) void load(false, true); }, 8000);
    const clear = () => { ++epoch.current; setPosts([]); setDetails(null); setEditor(null); setHistory(null); setComment(''); setCommentReferences([]); setMembers([]); setFollowing([]); };
    window.addEventListener('freecollab:auth-changed', clear);
    return () => { mounted.current = false; ++epoch.current; window.clearInterval(timer); window.removeEventListener('freecollab:auth-changed', clear); };
  }, [channelId, filter]);

  const targetRegion = useRef<HTMLElement>(null);
  useResourceTargetFocus(targetRegion, `${workspaceId}:${channelId}`, 'feed', focusPostId, !loading, async (id, sourceCurrent) => {
    if (!sourceCurrent() || !mayReplaceComment()) return false;
    const request = epoch.current, detail = await activityFeedApi.detail(channelId, id);
    if (!sourceCurrent() || !current(request) || detail.post.id !== id || detail.post.channel_id !== channelId) return false;
    detailId.current = id; setDetails(detail); setComment(''); setCommentReferences([]); setCommentEdit(null); setHistory(null);
    setSelected(detail.votes.find(vote => vote.created_by === actor)?.fields?.option_ids || []);
    return true;
  });
  async function act(key: string, action: (generation: number) => Promise<void>) {
    if (busy || !current(epoch.current)) return;
    const generation = epoch.current; setBusy(key); setError(''); setNotice('');
    try { await action(generation); } catch (failure) { if (current(generation)) setError(t(message(failure))); }
    finally { if (current(generation)) setBusy(''); }
  }
  async function open(post: FeedPost) {
    if (details?.post.id !== post.id && !mayReplaceComment()) return;
    await act('discussion', async generation => {
      const detail = await activityFeedApi.detail(channelId, post.id); if (!current(generation)) return;
      detailId.current = post.id; setDetails(detail); setComment(''); setCommentReferences([]); setCommentEdit(null); setHistory(null);
      setSelected(detail.votes.find(vote => vote.created_by === actor)?.fields?.option_ids || []);
    });
  }
  async function refreshDetail(generation: number) {
    if (!detailId.current) return;
    const result = await activityFeedApi.detail(channelId, detailId.current);
    if (current(generation)) setDetails(result);
  }
  function change<K extends keyof FeedPostFields>(key: K, value: FeedPostFields[K]) {
    setEditor(previous => {
      if (!previous) return previous;
      const fields = { ...previous.fields, [key]: value };
      const field_references = reconcileFieldReferences(feedReferenceTexts(previous.fields), feedReferenceTexts(fields), previous.fields.field_references);
      if (field_references) fields.field_references = field_references; else delete fields.field_references;
      return { ...previous, fields };
    });
  }
  function changeWritingField(name: string, value: string, refs: MessageReference[]) {
    if (Object.entries(editor?.fields.field_references || {}).reduce((count, [key, items]) => count + (key === name ? 0 : items.length), refs.length) > 50) { setError(t('Keep this item to at most 50 references.')); return; }
    setEditor(previous => {
      if (!previous) return previous;
      const fields = { ...previous.fields };
      if (name === 'title' || name === 'body') fields[name] = value;
      else if (fields.poll && name === 'poll.question') fields.poll = { ...fields.poll, question: value };
      else if (fields.poll && name.startsWith('poll.option:')) fields.poll = { ...fields.poll, options: fields.poll.options.map(option => 'poll.option:' + option.id === name ? { ...option, label: value } : option) };
      else return previous;
      const field_references = reconcileFieldReferences(feedReferenceTexts(previous.fields), feedReferenceTexts(fields), previous.fields.field_references, { [name]: refs });
      if (field_references) fields.field_references = field_references; else delete fields.field_references;
      return { ...previous, fields };
    });
  }
  function mayReplaceEditor() { return !editor || ((!editor.fields.title && !editor.fields.body && !editor.fields.poll) || window.confirm(t('Discard this unsaved update?'))); }
  function mayReplaceComment() { return !comment.trim() && !commentEdit || window.confirm(t('Changes have not saved yet. Leaving may discard them. Leave anyway?')); }
  function savePost(event: FormEvent) {
    event.preventDefault(); if (!editor) return;
    void act('publish', async generation => {
      await activityFeedApi.savePost(channelId, editor.fields, editor.previous); if (!current(generation)) return;
      setEditor(null); setNotice(t('Activity published.')); await load();
    });
  }
  function saveComment(event: FormEvent) {
    event.preventDefault(); if (!details) return;
    void act('comment', async generation => {
      await activityFeedApi.saveComment(channelId, details.post.id, { body: comment, ...(commentReferences.length ? { field_references: { body: commentReferences } } : {}) }, commentEdit || undefined);
      if (!current(generation)) return; setComment(''); setCommentReferences([]); setCommentEdit(null); setNotice(t('Comment saved.')); await refreshDetail(generation);
    });
  }
  const editable = (record: { created_by: string }) => record.created_by === actor || isAdmin;
  const displayed = posts.filter(post => !query || !!post.fields && (post.fields.title + '\n' + post.fields.body).toLocaleLowerCase(locale).includes(query.toLocaleLowerCase(locale)));
  const poll = details?.post.fields?.poll;
  const pollClosed = !!poll?.closes_at && Date.parse(poll.closes_at) <= Date.now();
  const ownVote = details?.votes.find(vote => vote.created_by === actor), ownReaction = details?.reactions.find(value => value.created_by === actor);
  const voters = details?.votes.filter(vote => !!vote.fields?.option_ids.length) || [];

  return <div className="fc-activity-feed">
    <section className="fc-account-section"><div className="fc-account-section-heading"><div><span className="fc-kicker">{t('Shared perspective')}</span><h3>{t('Activity feed')}</h3></div><Rss size={20} /></div>
      <p className="fc-account-muted">{t('Publish updates, discuss ideas and decide together. Each feed stays inside its channel.')}</p>
      <EnterpriseAlerts error={error} notice={notice} />
      <div className="fc-feed-toolbar"><button type="button" className="fc-button fc-button-primary" disabled={!!busy} onClick={() => { if (mayReplaceEditor()) setEditor({ fields: blank() }); }}><Plus size={15} />{t('Write an update')}</button><button type="button" className="fc-icon-button" aria-label={t('Refresh activity feed')} disabled={!!busy || loading} onClick={() => void load()}><RefreshCw size={16} /></button></div>
      <div className="fc-feed-filters"><label>{t('Show')}<select aria-label={t('Show')} className="fc-input" value={filter} disabled={!!busy || !!editor} onChange={event => setFilter(event.target.value as typeof filter)}><option value="all">{t('All updates')}</option><option value="following">{t('People I follow')}</option><option value="archived">{t('Archived updates')}</option></select></label><label>{t('Search loaded updates')}<input type="search" className="fc-input" value={query} onChange={event => setQuery(event.target.value)} /></label></div>
    </section>

    {editor && <form className="fc-account-section fc-feed-editor" aria-label={t('Activity editor')} onSubmit={savePost}><div className="fc-account-section-heading"><h3>{t(editor.previous ? 'Edit update' : 'New update')}</h3><button type="button" className="fc-icon-button" aria-label={t('Close activity editor')} disabled={!!busy} onClick={() => { if ((!editor.fields.title && !editor.fields.body && !editor.fields.poll) || window.confirm(t('Discard this unsaved update?'))) setEditor(null); }}><X size={16} /></button></div>
      <div className="fc-account-form"><label htmlFor={fieldId("feed-title")}>{t('Update title')}</label><CollaborationInput as="input" contextId={fieldContext("title")} references={editor.fields.field_references?.title} id={fieldId("feed-title")} className="fc-input" required maxLength={160} value={editor.fields.title} disabled={!!busy} onValueChange={(value, refs) => changeWritingField("title", value, refs)} /><label htmlFor={fieldId("feed-body")}>{t('Update text')}</label><div className="fc-feed-format"><button type="button" className="fc-account-link" disabled={!!busy} onClick={() => change('body', editor.fields.body + '**' + t('Bold text') + '**')}>{t('Bold')}</button><button type="button" className="fc-account-link" disabled={!!busy} onClick={() => change('body', editor.fields.body + '\n- ')}>{t('List')}</button><span>{t('Basic formatting; HTML is displayed as text.')}</span></div><CollaborationInput as="textarea" toolbar contextId={fieldContext("body")} references={editor.fields.field_references?.body} id={fieldId("feed-body")} className="fc-input fc-feed-composer" maxLength={20000} value={editor.fields.body} disabled={!!busy} onValueChange={(value, refs) => changeWritingField("body", value, refs)} /></div>
      <label className="fc-account-checkbox"><input type="checkbox" checked={!!editor.fields.poll} disabled={!!busy || !!editor.previous?.poll} onChange={event => change('poll', event.target.checked ? { id: activityFeedApi.identifier(), nonce: activityFeedApi.identifier(), question: '', options: [{ id: activityFeedApi.identifier(), label: '' }, { id: activityFeedApi.identifier(), label: '' }], multiple: false, closes_at: null } : null)} />{t('Include a poll')}</label>
      {editor.fields.poll && <fieldset className="fc-feed-poll-editor"><legend>{t('Poll')}</legend><div className="fc-account-form"><label htmlFor={fieldId("feed-question")}>{t('Poll question')}</label><CollaborationInput as="input" contextId={fieldContext("poll.question")} references={editor.fields.field_references?.["poll.question"]} id={fieldId("feed-question")} className="fc-input" required maxLength={240} value={editor.fields.poll.question} disabled={!!busy || !!editor.previous?.poll} onValueChange={(value, refs) => changeWritingField("poll.question", value, refs)} />{editor.fields.poll.options.map((option, index) => <div className="fc-feed-option-editor" key={option.id}><label htmlFor={fieldId("feed-option-" + option.id)}>{t('Option {count}', { count: index + 1 })}</label><CollaborationInput as="input" contextId={fieldContext("poll.option:" + option.id)} references={editor.fields.field_references?.["poll.option:" + option.id]} id={fieldId("feed-option-" + option.id)} className="fc-input" required maxLength={160} disabled={!!busy || !!editor.previous?.poll} value={option.label} onValueChange={(value, refs) => changeWritingField("poll.option:" + option.id, value, refs)} /><button type="button" className="fc-icon-button" aria-label={t('Remove option {count}', { count: index + 1 })} disabled={!!busy || !!editor.previous?.poll || editor.fields.poll!.options.length <= 2} onClick={() => change('poll', { ...editor.fields.poll!, options: editor.fields.poll!.options.filter(item => item.id !== option.id) })}><Trash2 size={14} /></button></div>)}</div><div className="fc-account-actions"><button type="button" className="fc-button" disabled={!!busy || !!editor.previous?.poll || editor.fields.poll.options.length >= 8} onClick={() => change('poll', { ...editor.fields.poll!, options: [...editor.fields.poll!.options, { id: activityFeedApi.identifier(), label: '' }] })}><Plus size={14} />{t('Add option')}</button><label className="fc-account-checkbox"><input type="checkbox" checked={editor.fields.poll.multiple} disabled={!!busy || !!editor.previous?.poll} onChange={event => change('poll', { ...editor.fields.poll!, multiple: event.target.checked })} />{t('Allow multiple choices')}</label></div><label className="fc-feed-deadline">{t('Poll deadline (optional)')}<input type="datetime-local" className="fc-input" disabled={!!busy || !!editor.previous?.poll} value={editor.fields.poll.closes_at ? (() => { const date = new Date(editor.fields.poll!.closes_at!); return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16); })() : ''} onChange={event => change('poll', { ...editor.fields.poll!, closes_at: event.target.value ? new Date(event.target.value).toISOString() : null })} /></label><p className="fc-account-muted">{t('Poll settings stay fixed after voting begins. Publish a new poll to change its meaning.')}</p></fieldset>}
      <div className="fc-account-actions"><button type="button" className="fc-button" disabled={!!busy} onClick={() => { if (window.confirm(t('Discard this unsaved update?'))) setEditor(null); }}>{t('Cancel')}</button><button type="submit" className="fc-button fc-button-primary" disabled={!!busy}>{t('Publish update')}</button></div>
    </form>}

    {loading && !posts.length && <p role="status">{t('Loading activity…')}</p>}
    <div className="fc-feed-posts">{displayed.map(post => <article className="fc-feed-post" key={post.id} data-feed-post={post.id}><header><div><span className="fc-kicker">{name(post.created_by)}</span><small>{when(post.revised_at)} · {t('Revision {count}', { count: post.revision })}</small></div><span className="fc-badge">{t(post.archived ? 'Archived' : (post.seen_revision || 0) >= post.revision ? 'Read' : 'Unread')}</span></header>
      {post.fields ? <><h3><FieldReferenceText value={post.fields.title} references={post.fields.field_references?.title} channelId={channelId} /></h3><SafeBody value={post.fields.body} references={post.fields.field_references?.body} channelId={channelId} />{post.fields.poll && <p className="fc-feed-poll-summary">{t('Poll')}: <FieldReferenceText value={post.fields.poll.question} references={post.fields.field_references?.["poll.question"]} channelId={channelId} /></p>}</> : <p className="fc-alert" role="alert">{t(post.resolution_error || 'Unlock this device to read encrypted activity.')}</p>}
      <div className="fc-account-actions"><button type="button" className="fc-button" disabled={!!busy} onClick={() => void open(post)}><MessageSquare size={14} />{t('Open discussion')}</button>{post.created_by !== actor && <button type="button" className="fc-account-link" disabled={!!busy} onClick={() => void act('follow', async generation => { await activityFeedApi.follow(channelId, post.created_by, !following.includes(post.created_by)); if (current(generation)) await load(); })}><UserPlus size={14} />{t(following.includes(post.created_by) ? 'Unfollow author' : 'Follow author')}</button>}{editable(post) && post.fields && <><button type="button" className="fc-account-link" disabled={!!busy} onClick={() => { if (mayReplaceEditor()) setEditor({ fields: structuredClone(post.fields!), previous: post }); }}><Pencil size={14} />{t('Edit')}</button><button type="button" className="fc-account-link" disabled={!!busy} onClick={() => void act('archive', async generation => { await activityFeedApi.savePost(channelId, post.fields!, post, { archived: !post.archived }); if (current(generation)) await load(); })}><Archive size={14} />{t(post.archived ? 'Restore update' : 'Archive update')}</button></>}<button type="button" className="fc-account-link" disabled={!!busy} onClick={() => void act('history', async generation => { const values = await activityFeedApi.history(channelId, post.id); if (current(generation)) setHistory(values); })}><History size={14} />{t('Versions')}</button></div>
    </article>)}</div>
    {!loading && !displayed.length && <p className="fc-account-muted">{t('No updates in this view yet.')}</p>}
    {next && <button type="button" className="fc-button" disabled={!!busy || loading} onClick={() => void load(true)}><ArrowDown size={15} />{t('Load older updates')}</button>}

    {details && <section ref={targetRegion} tabIndex={-1} data-resource-kind="feed" data-resource-id={details.post.id} className="fc-account-section fc-feed-discussion" aria-label={t('Activity discussion')}><div className="fc-account-section-heading"><div><span className="fc-kicker">{t('Discussion')}</span><h3>{details.post.fields ? <FieldReferenceText value={details.post.fields.title} references={details.post.fields.field_references?.title} channelId={channelId} /> : t('Encrypted update')}</h3></div><button type="button" className="fc-icon-button" aria-label={t('Close activity discussion')} onClick={() => { if (!mayReplaceComment()) return; detailId.current = ''; setDetails(null); setComment(''); setCommentReferences([]); setCommentEdit(null); }}><X size={16} /></button></div>
      {details.post.fields && <ReviewedResourceCopilot workspaceId={workspaceId} channelId={channelId} label={details.post.fields.title} snapshot={JSON.stringify([details.post.id, details.post.revision, details.post.fields])} text={details.post.fields.title + '\n' + details.post.fields.body + (details.post.fields.poll ? '\n' + JSON.stringify(details.post.fields.poll) : '')} />}
      <div className="fc-account-actions"><button type="button" className="fc-account-link" disabled={!!busy || !details.post.fields} onClick={() => void act('read', async generation => { await activityFeedApi.read(channelId, details.post); if (current(generation)) await load(); })}><Check size={14} />{t('Mark as read')}</button>{(['like', 'heart', 'celebrate'] as const).map(emoji => <button type="button" key={emoji} className={'fc-button fc-feed-reaction ' + (ownReaction?.fields?.emoji === emoji ? 'is-selected' : '')} aria-pressed={ownReaction?.fields?.emoji === emoji} aria-label={t(emoji === 'like' ? 'Like update' : emoji === 'heart' ? 'Love update' : 'Celebrate update')} disabled={!!busy || details.post.archived || !details.post.fields} onClick={() => void act('reaction', async generation => { await activityFeedApi.react(channelId, details.post.id, ownReaction?.fields?.emoji === emoji ? null : emoji, ownReaction); if (current(generation)) await refreshDetail(generation); })}>{emoji === 'like' ? <ThumbsUp size={14} /> : emoji === 'heart' ? <Heart size={14} /> : <span aria-hidden="true">✦</span>}<span>{details.reactions.filter(value => value.fields?.emoji === emoji).length}</span></button>)}</div>
      {poll && <fieldset className="fc-feed-poll"><legend><FieldReferenceText value={poll.question} references={details.post.fields?.field_references?.["poll.question"]} channelId={channelId} /></legend>{poll.options.map(option => { const count = voters.filter(vote => vote.fields!.option_ids.includes(option.id)).length; return <label key={option.id}><input type={poll.multiple ? 'checkbox' : 'radio'} name={fieldId("feed-poll-choice")} disabled={!!busy || details.post.archived || pollClosed} checked={selected.includes(option.id)} onChange={event => setSelected(poll.multiple ? event.target.checked ? [...selected, option.id] : selected.filter(id => id !== option.id) : [option.id])} /><span><FieldReferenceText value={option.label} references={details.post.fields?.field_references?.["poll.option:" + option.id]} channelId={channelId} /></span><small>{t('{count} votes', { count })}</small></label>; })}<p className="fc-account-muted">{t('{count} voters', { count: voters.length })}{poll.closes_at && ' · ' + t('Closes {date}', { date: when(poll.closes_at) })}</p><button type="button" className="fc-button fc-button-primary" disabled={!!busy || details.post.archived || pollClosed || !selected.length} onClick={() => void act('vote', async generation => { await activityFeedApi.vote(channelId, details.post, selected, ownVote); if (current(generation)) { setNotice(t('Your vote is saved.')); await refreshDetail(generation); } })}>{t(pollClosed ? 'Poll closed' : ownVote ? 'Update my vote' : 'Submit vote')}</button>{ownVote && <button type="button" className="fc-account-link" disabled={!!busy || pollClosed || details.post.archived} onClick={() => void act('clear-vote', async generation => { await activityFeedApi.vote(channelId, details.post, [], ownVote); if (current(generation)) { setSelected([]); await refreshDetail(generation); } })}>{t('Remove my vote')}</button>}</fieldset>}
      {(details.votes.some(vote => vote.resolution_error) || details.reactions.some(value => value.resolution_error)) && <p className="fc-alert" role="alert">{t('Some encrypted responses could not be verified and are excluded from counts.')}</p>}
      <h4>{t('Comments')}</h4><div className="fc-feed-comments">{details.comments.map(item => <article key={item.id}><header><strong>{name(item.created_by)}</strong><small>{when(item.revised_at)}</small></header>{item.archived ? <p className="fc-account-muted">{t('Comment archived')}</p> : item.fields ? <SafeBody value={item.fields.body} references={item.fields.field_references?.body} channelId={channelId} /> : <p role="alert">{t(item.resolution_error || 'Encrypted comment unavailable.')}</p>}<div className="fc-account-actions">{editable(item) && item.fields && !details.post.archived && <><button type="button" className="fc-account-link" disabled={!!busy} onClick={() => { if (!mayReplaceComment()) return; setCommentEdit(item); setComment(item.fields!.body); setCommentReferences(item.fields!.field_references?.body || []); }}>{t('Edit comment')}</button><button type="button" className="fc-account-link" disabled={!!busy} onClick={() => void act('comment-state', async generation => { await activityFeedApi.saveComment(channelId, details.post.id, item.fields!, item, !item.archived); if (current(generation)) await refreshDetail(generation); })}>{t(item.archived ? 'Restore comment' : 'Archive comment')}</button></>}<button type="button" className="fc-account-link" disabled={!!busy} onClick={() => void act('comment-history', async generation => { const values = await activityFeedApi.history(channelId, details.post.id, item.id); if (current(generation)) setHistory(values); })}>{t('Comment versions')}</button></div></article>)}{!details.comments.length && <p className="fc-account-muted">{t('Start the discussion.')}</p>}</div>
      {!details.post.archived && details.post.fields && <form className="fc-account-form" onSubmit={saveComment} aria-label={t('Activity comment editor')}><label htmlFor={fieldId("feed-comment")}>{t(commentEdit ? 'Edit your comment' : 'Add a comment')}</label><CollaborationInput as="textarea" toolbar contextId={`${instanceId || "primary"}:${channelId}:feed:${details.post.id}:comment:${commentEdit?.id || "new"}`} references={commentReferences} id={fieldId("feed-comment")} className="fc-input" required maxLength={4000} value={comment} disabled={!!busy} onValueChange={(value, refs) => { setComment(value); setCommentReferences(refs); }} /><div className="fc-account-actions">{commentEdit && <button type="button" className="fc-button" disabled={!!busy} onClick={() => { setCommentEdit(null); setComment(''); setCommentReferences([]); }}>{t('Cancel editing')}</button>}<button type="submit" className="fc-button fc-button-primary" disabled={!!busy || !comment.trim()}>{t('Save comment')}</button></div></form>}
    </section>}

    {history && <section className="fc-account-section" aria-label={t('Activity version history')}><div className="fc-account-section-heading"><h3>{t('Version history')}</h3><button type="button" className="fc-icon-button" aria-label={t('Close activity versions')} onClick={() => setHistory(null)}><X size={16} /></button></div><ol className="fc-feed-history">{history.map(row => <li key={row.revision}><strong>{t('Revision {count}', { count: row.revision })} · {name(row.revised_by)}</strong><small>{when(row.revised_at)}{row.archived ? ' · ' + t('Archived') : ''}</small>{row.fields ? <><h4>{'title' in row.fields ? <FieldReferenceText value={row.fields.title} references={row.fields.field_references?.title} channelId={channelId} /> : t('Comment')}</h4><SafeBody value={row.fields.body} references={row.fields.field_references?.body} channelId={channelId} /></> : <p role="alert">{t(row.resolution_error || 'Encrypted revision unavailable.')}</p>}</li>)}</ol></section>}
  </div>;
}
