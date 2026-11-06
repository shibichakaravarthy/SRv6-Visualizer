import {useCallback, useEffect, useLayoutEffect, useRef, useState} from 'react';
import {advanceEvents, type MotionClock} from './motion';
import type {Event} from './types';

export function usePlayback(key:string,events:Event[]) {
  const count=events.length;
  const [position,setPosition]=useState(0),[playing,setPlaying]=useState(false),[speed,setSpeed]=useState(1);
  // SVG motion reads this at frame rate. React controls update at 10 Hz or an event boundary.
  const clock=useRef<MotionClock>({position:0,listeners:new Set()}).current;
  const publish=useCallback((next:number)=>{clock.position=next;clock.listeners.forEach(listener=>listener());},[clock]);
  const seek=useCallback((value:number)=>{setPlaying(false);publish(value);setPosition(value);},[publish]);
  const reset=useCallback(()=>seek(0),[seek]);
  useLayoutEffect(()=>{reset();},[key,reset]);
  useEffect(()=>{
    const hide=()=>{if(document.hidden)setPlaying(false);};
    document.addEventListener('visibilitychange',hide);
    return ()=>document.removeEventListener('visibilitychange',hide);
  },[]);
  useEffect(()=>{
    if(!playing||!count)return;
    let frame=0,previous=0,lastRender=0;
    const tick=(time:number)=>{
      const old=clock.position;
      const next=advanceEvents(old,previous?time-previous:0,speed,events);
      previous=time;
      publish(next);
      if(Math.floor(old)!==Math.floor(next)||time-lastRender>=100||next>=count){setPosition(next);lastRender=time;}
      if(next>=count){setPlaying(false);return;}
      frame=requestAnimationFrame(tick);
    };
    frame=requestAnimationFrame(tick);
    return ()=>cancelAnimationFrame(frame);
  },[playing,speed,count,events,clock,publish]);
  const pause=useCallback(()=>{setPlaying(false);setPosition(clock.position);},[clock]);
  const play=()=>{if(!count)return;if(clock.position>=count){publish(0);setPosition(0);}setPlaying(true);};
  return {clock,position,index:Math.min(Math.floor(position),Math.max(0,count-1)),progress:position>=count?1:position%1,
    playing,speed,setSpeed,seek,reset,play,pause};
}
