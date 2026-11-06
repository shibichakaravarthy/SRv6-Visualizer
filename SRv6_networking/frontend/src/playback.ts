import type {Event, Link} from './types';
// Only a backend-observed physical transfer can produce an animated edge.
export function transferFor(event: Event | undefined, links: Link[]) {
  if (event?.kind !== 'transfer' || event.evidence !== 'observed') return undefined;
  return links.find(l => l.status === 'observed' && l.id === event.link_id &&
    ((l.source === event.source && l.target === event.target && l.source_interface === event.source_interface && l.target_interface === event.target_interface) ||
     (l.target === event.source && l.source === event.target && l.target_interface === event.source_interface && l.source_interface === event.target_interface)));
}
export function advance(position: number, deltaMs: number, speed: number, count: number) {
  return Math.min(count, position + Math.max(0, Math.min(deltaMs, 100)) * speed / 1400);
}
