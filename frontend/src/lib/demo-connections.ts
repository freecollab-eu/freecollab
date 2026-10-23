import type { DemoChannel, DemoDocument, DemoState } from './demo-showroom';

export interface DemoConnectionReason {
  kind: 'document_title' | 'shared_words' | 'stack_title';
  /** Readable words from the displayed content, never stems or a confidence claim. */
  words: string[];
}
interface SuggestionBase { id: string; channel: DemoChannel; reason: DemoConnectionReason; score: number }
export type DemoConnectionSuggestion =
  | (SuggestionBase & { kind: 'message_stack'; messageId: string; documentId: string; stackId: string })
  | (SuggestionBase & { kind: 'document_stack'; documentId: string; fromStackId: string; stackId: string })
  | (SuggestionBase & { kind: 'document_link'; documentId: string; relatedDocumentId: string });

const fold = (value: string) => value.normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase();
const stop = new Set(fold('a an the and or but of for with from to in on at by as is are was were be been being it its this that these those i ive im you your we our us they their them he she his her have has had do does did can could will would should may must shall yes no not then than here there all any some one two few new old first next last other another same very just still only more most less much many how what which who when where why into over under about through out up down so now also each every both either neither own my me ours theirs thanks thank please hello hi okay ok team shared share sharing project studio demo sample example document documents note notes work working make makes made give get take together everyone something things thing keep use use our room space step steps clear small version ready visible conversation discussion words text heading headings page pages group collection reference material idea ideas question questions feedback open start starting point feel welcoming warm durable before after bring left right important details easy long simple try generous new planeringens materialets och eller men att av för med från till i på vid som är var vara varit blir blev det den de dess denna detta dessa jag du ni vi vår vårt våra er ert era han hon hans hennes har hade ha kan kunde ska skall skulle bör får måste ja nej inte sedan än här där alla allt någon något några en ett två flera ny nya gammal gamla första nästa sista annan andra samma mycket bara ännu mer mest mindre hur vad vilket vilka vem när var vart varför genom ut upp ner så nu också varje båda egen egna min mitt mina din ditt dina tack snälla hej okej lag grupp gemensam gemensamt dela delar delat projekt exempel dokument anteckning anteckningar arbeta arbete gör göra ge få ta tillsammans alla sak saker håll använda utrymme plats steg tydlig tydliga liten små version klar redo synlig synliga samtal diskussion ord text rubrik rubriker sida sidor hög högar samling underlag idé idéer fråga frågor återkoppling öppen öppna börja början punkt känns välkomnande varm varma hållbar hållbara före efter ta med vänster höger viktig viktiga detalj detaljer enkelt enkel enkla prova gott stora gott om').split(/\s+/));
stop.delete('material');
const aliases = new Map<string, string>();
for (const words of [
  'sketch sketches sketched sketching skiss skisser skissen skisserna skissarbete',
  'material materials materialet materialen materialprov materialprover',
  'decision decisions decided agree agreed agreement beslut besluten besluta beslutade överens enades',
  'plan plans planning planned planering planen planera planerade',
  'design designs designed formgivning designen',
  'colour colours color colors palette palettes färg färger färgen färgerna palett paletten',
  'type typeface typefaces font fonts typography typsnitt typografi textstil',
  'table tables bord bordet borden',
  'door doors doorway doorways dörr dörrar dörren dörrarna dörröppning dörröppningen',
  'timber wood wooden trä trät träslag virke träprov',
  'brick bricks tegel teglet tegelsten tegelstenar',
  'stone stones sten stenar stenen stenarna',
  'courtyard courtyards garden gardens gård gården gårdar trädgård trädgården',
  'walkthrough walkthroughs genomgång genomgången genomgångar visning visningen rundvandring',
  'budget budgets budgeten budgetar',
  'contract contracts agreement agreements avtal avtalet avtalen kontrakt kontraktet',
  'invoice invoices faktura fakturan fakturor',
  'roadmap roadmaps färdplan färdplanen färdplaner',
  'meeting meetings möte möten mötet mötena',
  'deadline deadlines leveransdatum slutdatum tidsfrist',
  'prototype prototypes prototyp prototyper prototypen',
  'research researches forskning undersökning undersökningar',
]) {
  const values = fold(words).split(' ');
  for (const word of values) aliases.set(word, values[0]);
}

interface Profile { text: string; normalizedText: string; normalizedTitle: string; words: Map<string, string>; titleWords: Set<string> }
interface Match { score: number; keys: string[]; reasonKeys: string[]; exactTitle: boolean }
const limit = { documents: 30, messages: 100, stacks: 12, characters: 20000, words: 600, suggestions: 6 };
const plain = (value: string) => fold(value).replace(/[^\p{L}\p{N}]+/gu, ' ').trim().replace(/\s+/g, ' ');
const lexicalOrder = (left: string, right: string) => left < right ? -1 : left > right ? 1 : 0;
function tokens(value: string): Map<string, string> {
  const words = new Map<string, string>();
  // Links are navigational evidence, not permission to infer their remote contents.
  const text = value.slice(0, limit.characters).replace(/https?:\/\/\S+/gi, ' ');
  for (const match of text.matchAll(/[\p{L}\p{N}][\p{L}\p{N}’-]*/gu)) {
    const display = match[0], normalized = fold(display).replace(/’/g, "'");
    if (normalized.length < 3 || normalized.length > 50 || /^\d+$/.test(normalized) || stop.has(normalized)) continue;
    const key = aliases.get(normalized) ?? (normalized.length > 4 && normalized.endsWith('s') && !/(ss|us)$/.test(normalized) ? normalized.slice(0, -1) : normalized);
    if (!words.has(key)) words.set(key, display);
    if (words.size >= limit.words) break;
  }
  return words;
}
function profile(title: string, body: string): Profile {
  const titleTokens = tokens(title);
  const text = (title + '\n' + body).slice(0, limit.characters);
  return { text, normalizedText: ' ' + plain(text.replace(/https?:\/\/\S+/gi, ' ')) + ' ', normalizedTitle: plain(title), words: tokens(text), titleWords: new Set(titleTokens.keys()) };
}
function compare(source: Profile, target: Profile, rarity: (key: string) => number): Match | null {
  const keys = [...source.words.keys()].filter(key => target.words.has(key));
  if (!keys.length) return null;
  const phrase = target.normalizedTitle, sourceText = source.normalizedText;
  const exactTitle = phrase.length >= 4 && target.titleWords.size > 0 && sourceText.includes(' ' + phrase + ' ');
  let score = exactTitle ? 12 : 0;
  const strongest: { key: string; weight: number }[] = [];
  for (const key of keys) {
    const weight = rarity(key) * (target.titleWords.has(key) ? 2.2 : 1) * (source.titleWords.has(key) ? 1.25 : 1);
    score += weight;
    const position = strongest.findIndex(row => weight > row.weight || (weight === row.weight && lexicalOrder(key, row.key) < 0));
    if (position >= 0) strongest.splice(position, 0, { key, weight });
    else if (strongest.length < 3) strongest.push({ key, weight });
    if (strongest.length > 3) strongest.pop();
  }
  // Two topical words are required unless a document/stack is named explicitly.
  if (!exactTitle && (keys.length < 2 || score < 5)) return null;
  return { score, keys, reasonKeys: strongest.map(row => row.key), exactTitle };
}
const suggestionId = (kind: DemoConnectionSuggestion['kind'], channel: DemoChannel, source: string, target: string) => JSON.stringify([kind, channel, source, target]);
const readableReason = (match: Match, source: Profile, title: string, titleKind: 'document_title' | 'stack_title'): DemoConnectionReason => match.exactTitle
  ? { kind: titleKind, words: [title] }
  : { kind: 'shared_words', words: match.reasonKeys.map(key => source.words.get(key)!).filter(Boolean) };

/**
 * Conservative, local lexical suggestions for the public sample's current channel.
 * Reads only visible records already supplied by the caller. It does not classify
 * unsent drafts, mutate data, make requests, or claim semantic-model confidence.
 */
export interface DemoConnectionOptions { documentId?: string }
export function suggestDemoConnections(state: DemoState, channel: DemoChannel, translate: (source: string) => string = source => source, options: DemoConnectionOptions = {}): DemoConnectionSuggestion[] {
  if (!(['studio', 'product', 'design'] as string[]).includes(channel)) return [];
  const stacks = state.stacks[channel].slice(0, limit.stacks);
  const stackIds = new Set(stacks.map(stack => stack.id));
  const documents = state.documents.filter(document => document.channel === channel && stackIds.has(document.stackId)).slice(0, limit.documents);
  const focus = options.documentId !== undefined ? documents.find(row => row.id === options.documentId) : undefined;
  if (options.documentId !== undefined && !focus) return [];
  const focusedDocuments = focus ? [focus] : documents;
  const messages = state.messages[channel].slice(-limit.messages);
  const documentLinks = state.documentLinks?.[channel] ?? [];
  const dismissed = new Set(state.dismissedConnections?.[channel] ?? []);
  const displayed = (value: { sample: boolean }, text: string) => value.sample ? translate(text) : text;
  const titleOf = (document: DemoDocument) => displayed(document, document.title);
  const profiles = new Map(documents.map(document => [document.id, profile(titleOf(document), document.edited ?? displayed(document, document.source))]));
  const frequency = new Map<string, number>();
  for (const row of profiles.values()) for (const key of row.words.keys()) frequency.set(key, (frequency.get(key) ?? 0) + 1);
  const rarest = 1.3 + Math.log(documents.length + 1);
  const weights = new Map([...frequency].map(([key, count]) => [key, 1.3 + Math.log((documents.length + 1) / (count + 1))]));
  const rarity = (key: string) => weights.get(key) ?? rarest;
  const candidates: DemoConnectionSuggestion[] = [];
  const add = (suggestion: DemoConnectionSuggestion) => { if (!dismissed.has(suggestion.id)) candidates.push(suggestion); };

  for (const message of messages) {
    const source = profile('', displayed(message, message.text));
    let best: (DemoConnectionSuggestion & { kind: 'message_stack' }) | undefined;
    for (const document of focusedDocuments) {
      const stack = stacks.find(stack => stack.id === document.stackId)!;
      if (stack.references.includes(message.id) || stack.references.length >= 30) continue;
      const match = compare(source, profiles.get(document.id)!, rarity);
      if (!match) continue;
      const suggestion: DemoConnectionSuggestion & { kind: 'message_stack' } = { kind: 'message_stack', id: suggestionId('message_stack', channel, message.id, stack.id), channel, messageId: message.id, documentId: document.id, stackId: stack.id, score: match.score * 1.15, reason: readableReason(match, source, titleOf(document), 'document_title') };
      if (!best || suggestion.score > best.score || (suggestion.score === best.score && lexicalOrder(suggestion.documentId, best.documentId) < 0)) best = suggestion;
    }
    if (best) add(best);
  }

  const stackProfiles = new Map(stacks.map(stack => {
    const title = displayed(stack, stack.title);
    const body = documents.filter(row => row.stackId === stack.id).map(row => profiles.get(row.id)!.text).join('\n');
    return [stack.id, profile(title, body)];
  }));
  for (const document of focusedDocuments) {
    const source = profiles.get(document.id)!;
    const currentStack = stacks.find(stack => stack.id === document.stackId)!;
    const currentTitle = displayed(currentStack, currentStack.title);
    const otherDocuments = documents.filter(row => row.id !== document.id && row.stackId === document.stackId).map(row => profiles.get(row.id)!.text).join('\n');
    const current = compare(source, profile(currentTitle, otherDocuments), rarity)?.score ?? 0;
    const best = stacks.filter(stack => stack.id !== document.stackId).map(stack => {
      const title = displayed(stack, stack.title), target = stackProfiles.get(stack.id)!;
      return { stack, title, target, match: compare(source, target, rarity) };
    }).filter(row => row.match && row.match.score >= current + 4 && row.match.keys.some(key => row.target.titleWords.has(key) && source.titleWords.has(key)))
      .sort((a, b) => b.match!.score - a.match!.score || lexicalOrder(a.stack.id, b.stack.id))[0];
    if (best?.match) add({ kind: 'document_stack', id: suggestionId('document_stack', channel, document.id, best.stack.id), channel, documentId: document.id, fromStackId: document.stackId, stackId: best.stack.id, score: best.match.score, reason: readableReason(best.match, source, best.title, 'stack_title') });
  }

  // Similar collections must not recommend swapping their documents with each other.
  // In symmetric cases an explicit document link below is a clearer suggestion.
  const moves = candidates.filter((row): row is DemoConnectionSuggestion & { kind: 'document_stack' } => row.kind === 'document_stack');
  const ambiguousMoves = new Set<string>();
  for (const left of moves) for (const right of moves) {
    if (left.id !== right.id && left.fromStackId === right.stackId && left.stackId === right.fromStackId) {
      ambiguousMoves.add(left.score >= right.score + 4 ? right.id : left.id);
    }
  }

  for (let first = 0; first < documents.length; first++) for (let second = first + 1; second < documents.length; second++) {
    const left = documents[first], right = documents[second];
    if (focus && left.id !== focus.id && right.id !== focus.id) continue;
    // A stack already supplies this connection. Suggest explicit links across stacks.
    if (documentLinks.length >= 100 || left.stackId === right.stackId || documentLinks.some(link => (link.documentId === left.id && link.relatedDocumentId === right.id) || (link.documentId === right.id && link.relatedDocumentId === left.id))) continue;
    const leftProfile = profiles.get(left.id)!, rightProfile = profiles.get(right.id)!;
    const forwards = compare(leftProfile, rightProfile, rarity);
    const backwards = compare(rightProfile, leftProfile, rarity);
    const reverse = !!backwards && (!forwards || backwards.score > forwards.score || (backwards.score === forwards.score && lexicalOrder(right.id, left.id) < 0));
    const match = reverse ? backwards : forwards;
    if (!match || (!match.exactTitle && match.keys.length < 3)) continue;
    const [sourceId, targetId] = [left.id, right.id].sort(lexicalOrder);
    const source = reverse ? right : left, related = reverse ? left : right;
    add({ kind: 'document_link', id: suggestionId('document_link', channel, sourceId, targetId), channel, documentId: source.id, relatedDocumentId: related.id, score: match.score * .75, reason: readableReason(match, profiles.get(source.id)!, titleOf(related), 'document_title') });
  }

  const rank = (a: DemoConnectionSuggestion, b: DemoConnectionSuggestion) => b.score - a.score || lexicalOrder(a.id, b.id);
  const quotas: Record<DemoConnectionSuggestion['kind'], number> = { message_stack: 3, document_stack: 2, document_link: 2 };
  return candidates.filter(candidate => !ambiguousMoves.has(candidate.id)).sort(rank).filter(candidate => quotas[candidate.kind]-- > 0).slice(0, limit.suggestions);
}
