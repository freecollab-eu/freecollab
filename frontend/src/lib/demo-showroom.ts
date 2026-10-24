/** Public routes target the same application, bootstrapped with sample data. */
export type DemoSection = 'chat' | 'projects' | 'documents' | 'connections' | 'appearance';
export type DemoProvider = 'microsoft' | 'google';
export interface DemoDestination { section?: DemoSection; provider?: DemoProvider }
export const demoSections: DemoSection[] = ['chat', 'projects', 'documents', 'connections', 'appearance'];
export function readDemoDestination(hash: string): DemoDestination | null {
  const params = new URLSearchParams(hash.replace(/^#/, ''));
  if (!params.has('demo')) return null;
  const section = demoSections.find(value => value === params.get('demo')) ?? 'chat';
  const provider = params.get('provider');
  return section === 'connections' && (provider === 'microsoft' || provider === 'google') ? { section, provider } : { section };
}
export function demoDestinationHash(destination: DemoDestination = {}): string {
  const section = destination.section && demoSections.includes(destination.section) ? destination.section : 'chat';
  if (section === 'chat') return '#demo';
  const params = new URLSearchParams({ demo: section });
  if (section === 'connections' && (destination.provider === 'microsoft' || destination.provider === 'google')) params.set('provider', destination.provider);
  return '#' + params.toString();
}
