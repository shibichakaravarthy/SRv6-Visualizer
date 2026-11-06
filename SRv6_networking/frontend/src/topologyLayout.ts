import type {Device, Link} from './types';

const endpoint = (node: Device) => ['host', 'client', 'server'].includes(node.role);
const compare = (a: Device, b: Device) => a.name.localeCompare(b.name, undefined, {numeric: true}) || a.id.localeCompare(b.id);

/** Clos-style drawing only. Levels are not forwarding paths or inferred links. */
export function closLayout(devices: Device[], links: Link[]): Record<string, {x: number; y: number}> {
  const known = new Map(devices.map(node => [node.id, node]));
  const neighbors = new Map(devices.map(node => [node.id, new Set<string>()]));
  for (const link of links) {
    if (!link.source || !link.target || !known.has(link.source) || !known.has(link.target)) continue;
    neighbors.get(link.source)!.add(link.target);
    neighbors.get(link.target)!.add(link.source);
  }
  // Prefer a client-facing boundary, independently of Docker's discovery order.
  const rank = (node: Device) => node.role === 'client' || /client|^pc\d/i.test(node.name) ? 0 : endpoint(node) ? 1 : 2;
  const ordered = [...devices].sort((a, b) => rank(a) - rank(b) || compare(a, b));
  const positions: Record<string, {x: number; y: number}> = {};
  let top = 0;
  for (const root of ordered) {
    if (positions[root.id]) continue;
    const levels = new Map<string, number>([[root.id, 0]]);
    // Hosts attached to the same access device share a boundary column.
    if (endpoint(root)) {
      for (const access of neighbors.get(root.id)!) {
        for (const peer of neighbors.get(access)!) {
          if (endpoint(known.get(peer)!)) levels.set(peer, 0);
        }
      }
    }
    const queue = [...levels.keys()];
    for (let i = 0; i < queue.length; i++) {
      const id = queue[i];
      for (const next of neighbors.get(id)!) {
        if (!levels.has(next)) {levels.set(next, levels.get(id)! + 1); queue.push(next);}
      }
    }
    const columns = new Map<number, Device[]>();
    for (const [id, level] of levels) columns.set(level, [...(columns.get(level) || []), known.get(id)!]);
    const height = Math.max(...[...columns.values()].map(nodes => nodes.length)) * 155;
    let x = 0;
    for (const level of [...columns.keys()].sort((a,b) => a-b)) {
      const column = columns.get(level)!.sort(compare);
      column.forEach((node, row) => {positions[node.id] = {x, y: top + height / 2 + (row - (column.length - 1) / 2) * 155};});
      // More space for crossing links between populated core stages.
      const next = columns.get(level + 1) || [];
      x += column.length > 1 && next.length > 1 ? 250 : 150;
    }
    top += height + 180;
  }
  return positions;
}
