import {memo,useLayoutEffect,useRef} from 'react';
import {ViewportPortal} from '@xyflow/react';
import {motionPoint,type Geometry,type MotionClock} from '../motion';
import type {Event} from '../types';

export const PacketMarker=memo(function PacketMarker({clock,event,index,geometry,direction}:{clock:MotionClock;event?:Event;index:number;geometry?:Geometry;direction:string}){
  const marker=useRef<SVGGElement>(null),arrow=useRef<SVGPathElement>(null),label=useRef<SVGTextElement>(null);
  useLayoutEffect(()=>{
    const update=()=>{
      if(!marker.current||!geometry)return;
      const t=Math.max(0,Math.min(1,clock.position-index));
      const point=motionPoint(geometry,t);
      const ahead=motionPoint(geometry,Math.min(1,t+.01)),behind=motionPoint(geometry,Math.max(0,t-.01));
      marker.current.setAttribute('transform',`translate(${point.x},${point.y})`);
      const dx=ahead.x-behind.x,dy=ahead.y-behind.y;
      if(dx||dy)arrow.current?.setAttribute('transform',`rotate(${Math.atan2(dy,dx)*180/Math.PI})`);
      const state=(event?.kind==='processing'&&t<.5?event.before:event?.after)?.state;
      const format=state?.srh_present?'IPv6 + SRH':state?.encapsulation?.layers?.length?'IPv6 encapsulated':'IPv6';
      if(label.current)label.current.textContent=`${direction==='reply'?'Reply':'Request'} · ${format}`;
    };
    update();clock.listeners.add(update);return ()=>{clock.listeners.delete(update);};
  },[clock,index,geometry,event,direction]);
  if(!geometry)return null;
  const reply=direction==='reply';
  return <ViewportPortal><svg className={`packet-overlay ${reply?'reply':'request'}`} width="1" height="1" overflow="visible" aria-label={`Observed echo ${direction} packet`}>
    <g ref={marker} data-testid="packet-marker">
      <circle r="21" fill={reply?'#d9f4fc':'#fff0bf'} opacity=".85"/>
      <rect x="-12" y="-9" width="24" height="18" rx="4" fill={reply?'#41b9d7':'#f8bd44'} stroke={reply?'#14596e':'#83530b'} strokeWidth="1.5"/>
      <path d="m-10-6 10 8 10-8" fill="none" stroke={reply?'#14596e':'#83530b'}/>
      <path ref={arrow} d="M27 -4 L34 0 L27 4 Z" fill={reply?'#1685a4':'#b77b13'}/>
      <text ref={label} y="-30" textAnchor="middle" fontSize="11" fontWeight="600" paintOrder="stroke" stroke="white" strokeWidth="3" fill={reply?'#14596e':'#83530b'}/>
    </g>
  </svg></ViewportPortal>;
});
