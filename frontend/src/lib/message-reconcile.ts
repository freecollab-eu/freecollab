import type { Message } from './api';

/** Preserve bounded history, retain edits/deletions and order missed new events. */
export function reconcileMessageChanges(current:Message[],updates:Message[],parentId?:string):Message[] {
  const time=(value:string)=>Date.parse(/[zZ]$|[+-]\d\d:\d\d$/.test(value)?value:value+'Z');
  const byId=new Map(current.map(message=>[message.id,message]));
  const oldest=current.length?current.reduce((value,message)=>Math.min(value,time(message.created_at)),Infinity):0;
  for(const message of updates){
    if(parentId?message.parent_id!==parentId:!!message.parent_id)continue;
    if(byId.has(message.id)||parentId||time(message.created_at)>=oldest)byId.set(message.id,message);
  }
  return [...byId.values()].sort((a,b)=>time(a.created_at)-time(b.created_at)||a.id.localeCompare(b.id));
}
