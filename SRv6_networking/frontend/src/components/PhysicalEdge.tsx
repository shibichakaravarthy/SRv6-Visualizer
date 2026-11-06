import {BaseEdge, getStraightPath, type EdgeProps} from '@xyflow/react';
export function PhysicalEdge(props: EdgeProps) {
  const [path] = getStraightPath(props);
  const dx = props.targetX - props.sourceX, dy = props.targetY - props.sourceY;
  const length = Math.hypot(dx, dy) || 1;
  const offset = Math.min(42, length * .22);
  const labels = [
    {text: props.data?.sourcePort, x: props.sourceX + dx / length * offset, y: props.sourceY + dy / length * offset},
    {text: props.data?.targetPort, x: props.targetX - dx / length * offset, y: props.targetY - dy / length * offset},
  ];
  return <>
    <BaseEdge id={props.id} path={path} style={props.style} interactionWidth={16}/>
    {props.data?.ports && labels.map((label, i) => <g key={i} transform={`translate(${label.x},${label.y})`} className="port-label" pointerEvents="none">
      <rect x="-17" y="-8" width="34" height="16" rx="3" fill={props.data?.active ? '#fff1bf' : 'white'} stroke={props.data?.active ? '#e39b19' : '#cbd5da'}/>
      <text textAnchor="middle" dominantBaseline="central" fontSize="10" fill="#3d5562">{String(label.text || '')}</text>
    </g>)}
  </>;
}
