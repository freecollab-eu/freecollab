import type { DocumentOrganization } from './document-organization-api';
import type { SharedDocumentRecord } from './shared-documents-api';

export interface DocumentStackSuggestion {
  documentId: string;
  stackId: string;
  stackTitle: string;
  terms: string[];
  identity: string;
  dismissKey: string;
}

const words = (text: string) => new Set((text.toLocaleLowerCase().match(/[\p{L}\p{N}]{3,}/gu) || [])
  .filter(word => !['the', 'and', 'for', 'with', 'och', 'för', 'med', 'ett', 'det', 'den', 'notes', 'document', 'untitled', 'dokument', 'anteckningar'].includes(word)));
const revision = (document: SharedDocumentRecord) => [document.id, document.title, document.version, document.updated_at, document.encrypted_source_version];

/** One local, reviewable proposal from currently visible metadata; never reads a page or contacts a provider. */
export function suggestDocumentStack(document: SharedDocumentRecord, organization: DocumentOrganization): DocumentStackSuggestion | null {
  if (!organization.supported || organization.organization_conflicts?.length || document.archived || document.resolution_error) return null;
  const visible = organization.documents.filter(row => row.channel_id === document.channel_id && !row.archived && !row.resolution_error);
  const source = visible.find(row => row.id === document.id);
  if (!source) return null;
  const placement = organization.placements.find(row => row.channel_id === document.channel_id && row.document_id === source.id);
  const comments = organization.stack_comments.filter(row => row.channel_id === document.channel_id && row.document_id === source.id && !row.resolved);
  const sourceWords = words([source.title, ...comments.map(row => row.text)].join(' '));
  const candidates = organization.stacks.filter(stack => stack.channel_id === source.channel_id && stack.id !== placement?.stack_id).map(stack => {
    const members = visible.filter(row => row.id !== source.id && organization.placements.some(placed => placed.channel_id === source.channel_id && placed.document_id === row.id && placed.stack_id === stack.id));
    const titleTerms = [...words(stack.title)].filter(word => sourceWords.has(word));
    const memberTerms = [...new Set(members.flatMap(row => [...words(row.title || '')].filter(word => sourceWords.has(word))))];
    const terms = [...new Set([...titleTerms, ...memberTerms])].sort();
    return { stack, members, terms, score: titleTerms.length * 3 + memberTerms.length };
  }).filter(row => row.score > 0).sort((a, b) => b.score - a.score || a.stack.id.localeCompare(b.stack.id));
  const best = candidates[0];
  // Equally supported destinations do not need another prompt in the workspace.
  if (!best || candidates[1]?.score === best.score) return null;
  return {
    documentId: source.id, stackId: best.stack.id, stackTitle: best.stack.title, terms: best.terms,
    dismissKey: JSON.stringify([source.id, best.stack.id, best.stack.generation]),
    identity: JSON.stringify([revision(source), placement ? [placement.stack_id, placement.version] : null,
      [best.stack.id, best.stack.generation, best.stack.version, best.stack.title],
      best.members.map(row => [revision(row), organization.placements.find(placed => placed.document_id === row.id)?.version]).sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b))),
      comments.map(row => [row.id, row.version, row.text]).sort((a, b) => String(a[0]).localeCompare(String(b[0])))]),
  };
}
