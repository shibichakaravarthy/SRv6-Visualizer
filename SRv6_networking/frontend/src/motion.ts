import type {Event, Link} from './types';
import {transferFor} from './playback.ts';

export interface Point {x:number; y:number}
export interface Geometry {start:Point; end:Point; center?:Point}
export interface MotionNode {id:string; position:Point; measured?:{width?:number; height?:number}}
export interface MotionClock {position:number; listeners:Set<() => void>}

const mix=(a:Point,b:Point,t:number):Point=>({x:a.x+(b.x-a.x)*t,y:a.y+(b.y-a.y)*t});
const ease=(t:number)=>t*t*(3-2*t);

/** Only captured interfaces on observed links supply marker coordinates. */
export function eventGeometry(event:Event|undefined,nodes:MotionNode[],links:Link[]):Geometry|undefined {
  if(!event || event.evidence!=='observed')return;
  const byId=new Map(nodes.map(n=>[n.id,n]));
  const center=(n:MotionNode)=>({x:n.position.x+(n.measured?.width || 104)/2,y:n.position.y+(n.measured?.height || 94)/2});
  const port=(id:string,iface:string,link?:Link)=>{
    const edge=link || links.find(l=>l.status==='observed'&&((l.source===id&&l.source_interface===iface)||(l.target===id&&l.target_interface===iface)));
    if(!edge)return;
    const node=byId.get(id),peer=byId.get(edge.source===id?edge.target!:edge.source!);
    if(!node||!peer)return;
    const right=edge.source===id?node.position.x<=peer.position.x:node.position.x<peer.position.x;
    return {x:node.position.x+(right?(node.measured?.width || 104):0),y:center(node).y};
  };
  if(event.kind==='transfer'){
    const link=transferFor(event,links);
    if(!link)return;
    const start=port(event.source,event.source_interface,link),end=port(event.target,event.target_interface,link);
    if(start&&end)return {start,end};
  }else if(event.kind==='processing'){
    const node=byId.get(event.node);
    const start=port(event.node,event.before.interface),end=port(event.node,event.after.interface);
    if(node&&start&&end)return {start,end,center:center(node)};
  }
}

/** A processing pause connects the captured ingress and egress ports within one node. */
export function motionPoint(geometry:Geometry,progress:number):Point {
  const t=Math.max(0,Math.min(1,progress));
  if(!geometry.center)return mix(geometry.start,geometry.end,ease(t));
  if(t<.25)return mix(geometry.start,geometry.center,ease(t/.25));
  if(t<.65)return geometry.center;
  return mix(geometry.center,geometry.end,ease((t-.65)/.35));
}

export function advanceEvents(position:number,deltaMs:number,speed:number,events:Event[]):number {
  let remaining=Math.max(0,Math.min(deltaMs,100))*speed;
  while(position<events.length && remaining>0){
    const index=Math.floor(position), duration=events[index].kind==='processing'?700:1100;
    const needed=(1-(position-index))*duration;
    if(remaining<needed)return position+remaining/duration;
    remaining-=needed;position=index+1;
  }
  return Math.min(position,events.length);
}
