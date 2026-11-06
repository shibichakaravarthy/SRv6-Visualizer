import {useEffect, useMemo, useRef, useState} from 'react';
import {ReactFlow, Background, Controls, MiniMap, Handle, Position, applyNodeChanges, BaseEdge, getStraightPath, type Node, type NodeProps, type EdgeProps} from '@xyflow/react';
import {DeviceIcon} from './DeviceIcon';
import {PhysicalEdge} from './PhysicalEdge';
import {closLayout} from '../topologyLayout';
import {PacketMarker} from './PacketMarker';
import {eventGeometry, type MotionClock} from '../motion';
import {transferFor} from '../playback';
import type {Device, Event, Link} from '../types';

function DeviceNode({data}: NodeProps) {
  return <div className={`device ${data.active ? 'active' : ''} ${data.selected ? 'selected' : ''}`}>
    <Handle id="in-left" type="target" position={Position.Left}/><Handle id="out-right" type="source" position={Position.Right}/><Handle id="out-left" type="source" position={Position.Left}/><Handle id="in-right" type="target" position={Position.Right}/>
    <DeviceIcon name={data.role === 'bridge' ? 'sw' : data.role === 'server' || /server/i.test(String(data.name)) ? 'server' : ['host', 'client'].includes(String(data.role)) ? 'client' : String(data.name)}/>
    <strong>{String(data.name)}</strong><small><i className={data.running ? 'up' : 'down'}/> {String(data.role)}</small>
    {data.showAddresses ? <small className="address">{String(data.address || 'No data-plane address')}</small> : null}
  </div>;
}
function EvidenceEdge(props: EdgeProps) {
  const [path] = getStraightPath(props);
  return <><PhysicalEdge {...props}/>{props.data?.active ?
    <BaseEdge id={`${props.id}-active`} path={path} style={{stroke: props.data.direction === 'reply' ? '#28a9c7' : '#e39b19', strokeWidth: 4}}/>
    : null}</>;
}
const nodeTypes = {device: DeviceNode}, edgeTypes = {evidence: EvidenceEdge};

export function Topology({devices, links, selected, onSelect, event, progress, direction, clock, index}: {
  devices: Device[]; links: Link[]; selected: string; onSelect: (id: string) => void; event?: Event; progress: number; direction: string; clock: MotionClock; index: number;
}) {
  const [nodes, setNodes] = useState<Node[]>([]), [ports, setPorts] = useState(true), [addresses, setAddresses] = useState(false), [reset, setReset] = useState(0);
  const fit = useRef<(() => void) | null>(null);
  const key = `srv6-clos-layout-v1:${[...new Set(devices.map(d => d.lab_id))].sort().join(':')}`;
  const resolved = useMemo(() => links.map(l => ({...l, source: l.source || devices.find(d => d.name === l.source_name)?.id, target: l.target || devices.find(d => d.name === l.target_name)?.id})), [links, devices]);
  useEffect(() => {
    let positions: Record<string, {x: number; y: number}> = {};
    try {positions = JSON.parse(localStorage.getItem(key) || '{}');} catch { /* Storage is optional. */ }
    const defaults = closLayout(devices, resolved);
    setNodes(devices.map(d => ({id: d.id, type: 'device', position: Number.isFinite(positions[d.id]?.x) && Number.isFinite(positions[d.id]?.y) ? positions[d.id] : defaults[d.id], data: {...d}})));
  }, [devices, resolved, key, reset]);
  useEffect(() => {
    if (!reset) return;
    const frame = requestAnimationFrame(() => {fit.current?.();});
    return () => cancelAnimationFrame(frame);
  }, [reset, nodes.length]);
  const active = transferFor(event, links);
  const activeNode = event?.kind === 'processing' ? event.node : active ? (progress < .04 ? event?.source : progress > .96 ? event?.target : undefined) : undefined;
  const shown = useMemo(() => nodes.map(n => ({...n, data: {...n.data, selected: n.id === selected,
    active: n.id === activeNode, showAddresses: addresses,
    address: devices.find(d => d.id === n.id)?.addresses[0]?.address}})), [nodes, selected, activeNode, addresses, devices]);
  const geometry = useMemo(() => eventGeometry(event, nodes, links), [event, nodes, links]);
  const edges = useMemo(() => {
    const positions = new Map(nodes.map(n => [n.id, n.position]));
    return resolved.filter(l => l.source && l.target).map(l => ({id: l.id, source: l.source!, target: l.target!, type: 'evidence',
      sourceHandle: (positions.get(l.source!)?.x || 0) > (positions.get(l.target!)?.x || 0) ? 'out-left' : 'out-right',
      targetHandle: (positions.get(l.source!)?.x || 0) > (positions.get(l.target!)?.x || 0) ? 'in-right' : 'in-left',
      style: {stroke: l.status === 'observed' ? l.operational === 'up' ? '#52b87e' : '#d86868' : '#a5afbd', strokeWidth: 2, strokeDasharray: l.status === 'observed' ? undefined : '5 5'},
      data: {link: l, ports, sourcePort: l.source_interface, targetPort: l.target_interface, active: active?.id === l.id, direction}}));
  }, [nodes, resolved, ports, active?.id, direction]);
  return <section className="topology"><div className="canvas-tools"><span>CLOS WORKSPACE <b>{devices.length} devices · {links.filter(l => l.status === 'observed').length} verified links</b></span><label><input type="checkbox" checked={ports} onChange={e => setPorts(e.target.checked)}/> Interfaces</label><label><input type="checkbox" checked={addresses} onChange={e => setAddresses(e.target.checked)}/> IPv6</label><button onClick={() => {try {localStorage.removeItem(key);} catch {} setReset(v => v + 1);}}>Clos layout</button></div>
    {!devices.length && <div className="empty">Refresh the topology to discover your Containerlab devices.</div>}
    <ReactFlow onInit={instance => {fit.current = () => {void instance.fitView({padding:.08, duration:250});};}} nodes={shown} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes} onNodesChange={changes => setNodes(n => applyNodeChanges(changes, n))}
      onNodeClick={(_, node) => onSelect(node.id)} onNodeDragStop={(_, node) => {try {localStorage.setItem(key, JSON.stringify(Object.fromEntries(nodes.map(n => [n.id, n.id === node.id ? node.position : n.position]))));} catch {}}}
      fitView fitViewOptions={{padding:.08}} minZoom={.1} maxZoom={2} nodesConnectable={false} deleteKeyCode={null}>
      <PacketMarker clock={clock} event={event} index={index} geometry={geometry} direction={direction}/>
      <Background gap={24} color="#d5dfe7"/><Controls showInteractive={false}/><MiniMap pannable zoomable/>
    </ReactFlow><div className="legend">Solid: verified connection · Dashed: configured only · Amber: request · Cyan: reply · Drag to arrange, scroll to zoom</div>
  </section>;
}
