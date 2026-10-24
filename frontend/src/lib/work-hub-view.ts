export type WorkHubTab = 'shared-work' | 'tasks' | 'meetings' | 'documents' | 'workflows' | 'data' | 'mobile' | 'feed' | 'copilot' | 'cloud' | 'external-meetings' | 'apps';

export interface WorkHubViewScope {
  userId: string | null;
  credential: string | null;
  workspaceId: string | undefined;
  channelId: string;
  secure: boolean;
}

const tabs: readonly WorkHubTab[] = ['shared-work', 'tasks', 'meetings', 'documents', 'workflows', 'data', 'mobile', 'feed', 'copilot', 'cloud', 'external-meetings', 'apps'];

/** Navigation preferences only. Contents, drafts and authority stay in their tools. */
export function createWorkHubViewMemory() {
  let owner: Pick<WorkHubViewScope, 'userId' | 'credential' | 'workspaceId'> | null = null;
  const views = new Map<string, { tab: WorkHubTab; secure: boolean }>();
  const clear = () => { owner = null; views.clear(); };
  const matches = (scope: WorkHubViewScope) => {
    if (!scope.userId || !scope.credential || !scope.workspaceId || !scope.channelId) { clear(); return false; }
    if (!owner || owner.userId !== scope.userId || owner.credential !== scope.credential || owner.workspaceId !== scope.workspaceId) {
      clear(); owner = { userId: scope.userId, credential: scope.credential, workspaceId: scope.workspaceId };
    }
    return true;
  };
  return {
    clear,
    recall(scope: WorkHubViewScope): WorkHubTab {
      if (!matches(scope)) return 'tasks';
      const view = views.get(scope.channelId);
      if (!view || view.secure !== scope.secure) { views.delete(scope.channelId); return 'tasks'; }
      views.delete(scope.channelId); views.set(scope.channelId, view);
      return view.tab;
    },
    remember(scope: WorkHubViewScope, tab: WorkHubTab) {
      if (!matches(scope) || !tabs.includes(tab) || tab === 'shared-work' && !scope.secure) return;
      views.delete(scope.channelId); views.set(scope.channelId, { tab, secure: scope.secure });
      while (views.size > 24) views.delete(views.keys().next().value!);
    },
  };
}
