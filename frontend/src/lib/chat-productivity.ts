import { api, type Channel } from './api';
import { getSecureMessaging } from './secure-client';
import type { MessageReference } from './message-references';

export interface GroupConversation extends Channel { participants: Array<{id:string;name:string}>; created?: boolean }
export interface ScheduledMessage { id:string;channel_id:string;due_at:string;status:string;content?:string;references?:MessageReference[];failure_code?:string;message_id?:string;parent_id?:string }
export interface MessageReminder { id:string;channel_id:string;item_id:string;kind:'message'|'matrix';due_at:string;status:string;completed_at?:string }
const segment = encodeURIComponent;
export const chatProductivity = {
  groups: (workspaceId:string) => api.request<GroupConversation[]>('/group-conversations?workspace_id='+segment(workspaceId)),
  openGroup: (workspaceId:string, participantIds:string[]) => api.request<GroupConversation>('/group-conversations', {method:'POST',body:JSON.stringify({workspace_id:workspaceId,participant_ids:participantIds})}),
  leaveGroup: (id:string) => api.request('/group-conversations/'+segment(id)+'/leave',{method:'POST'}),
  scheduled: (channelId:string) => api.request<ScheduledMessage[]>('/channels/'+segment(channelId)+'/scheduled-messages'),
  schedule: (channelId:string,content:string,mentions:string[],dueAt:string,references:MessageReference[]=[]) => api.request<ScheduledMessage>('/channels/'+segment(channelId)+'/scheduled-messages', {method:'POST',body:JSON.stringify({client_id:crypto.randomUUID(),content,mentions,references,due_at:dueAt})}),
  cancel: (id:string) => api.request('/scheduled-messages/'+segment(id),{method:'DELETE'}),
  reminders: (channelId:string) => api.request<MessageReminder[]>('/channels/'+segment(channelId)+'/reminders'),
  async remind(channelId:string,messageId:string,dueAt:string) {
    const encrypted = await (await getSecureMessaging()).handles(channelId);
    return api.request<MessageReminder>('/channels/'+segment(channelId)+'/reminders',{method:'POST',body:JSON.stringify({due_at:dueAt,...encrypted?{matrix_event_id:messageId}:{message_id:messageId}})});
  },
  completeReminder: (id:string) => api.request('/reminders/'+segment(id),{method:'PATCH',body:JSON.stringify({completed:true})}),
  snoozeReminder: (id:string,dueAt:string) => api.request('/reminders/'+segment(id),{method:'PATCH',body:JSON.stringify({due_at:dueAt})}),
  completeSaved: (channelId:string,itemId:string,completed:boolean) => api.request('/bookmarks/'+segment(channelId)+'/'+segment(itemId)+'/completion',{method:'PATCH',body:JSON.stringify({completed})}),
};
