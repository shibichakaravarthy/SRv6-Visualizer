import assert from 'node:assert/strict';
import {test} from 'node:test';
import {advance, transferFor} from './playback.ts';
const link = {id:'l',source:'a',target:'b',source_interface:'eth1',target_interface:'eth2',status:'observed'};
const event = {kind:'transfer',evidence:'observed',link_id:'l',source:'a',target:'b',source_interface:'eth1',target_interface:'eth2'};
test('only observed matching physical links animate',()=>{
  assert.equal(transferFor(event as any,[link as any]),link);
  assert.equal(transferFor({...event,evidence:'inferred'} as any,[link as any]),undefined);
  assert.equal(transferFor({...event,target:'c'} as any,[link as any]),undefined);
  assert.equal(transferFor(event as any,[{...link,status:'configured_only'} as any]),undefined);
});
test('reply direction is checked against reversed interfaces',()=>{
  assert.equal(transferFor({...event,source:'b',target:'a',source_interface:'eth2',target_interface:'eth1'} as any,[link as any]),link);
  assert.equal(transferFor({...event,source:'b',target:'a'} as any,[link as any]),undefined);
});
test('clock clamps tab stalls, speed, and end of journey',()=>{
  assert.equal(advance(0,14000,1,5),100/1400);
  assert.equal(advance(4.99,100,4,5),5);
  assert.equal(advance(1,0,2,5),1);
});

// Motion uses captured ports, including same-node processing, without filling evidence gaps.
import {advanceEvents,eventGeometry,motionPoint} from './motion.ts';
const nodes=[{id:'a',position:{x:0,y:0},measured:{width:100,height:80}},{id:'b',position:{x:300,y:0},measured:{width:100,height:80}},{id:'c',position:{x:600,y:0},measured:{width:100,height:80}}];
const nextLink={id:'next',source:'b',target:'c',source_interface:'eth3',target_interface:'eth4',status:'observed'};
const processing={kind:'processing',evidence:'observed',node:'b',before:{interface:'eth2'},after:{interface:'eth3'}};
test('packet stays continuous from link into router and out to the next link',()=>{
  const arrival=eventGeometry(event as any,nodes,[link,nextLink] as any)!;
  const inside=eventGeometry(processing as any,nodes,[link,nextLink] as any)!;
  const departure=eventGeometry({...event,link_id:'next',source:'b',target:'c',source_interface:'eth3',target_interface:'eth4'} as any,nodes,[link,nextLink] as any)!;
  assert.deepEqual(motionPoint(arrival,1),motionPoint(inside,0));
  assert.deepEqual(motionPoint(inside,1),motionPoint(departure,0));
  assert.deepEqual(motionPoint(inside,.4),{x:350,y:40});
  assert.deepEqual(motionPoint(inside,.6),{x:350,y:40});
});
test('reply moves left and missing interfaces never manufacture motion',()=>{
  const reply={...event,source:'b',target:'a',source_interface:'eth2',target_interface:'eth1'};
  const geometry=eventGeometry(reply as any,nodes,[link] as any)!;
  assert.ok(motionPoint(geometry,0).x>motionPoint(geometry,1).x);
  assert.equal(eventGeometry({...processing,after:{interface:'missing'}} as any,nodes,[link,nextLink] as any),undefined);
  assert.equal(eventGeometry(event as any,nodes,[{...link,status:'configured_only'}] as any),undefined);
});
test('event clock carries unused frame time across processing boundaries',()=>{
  const events=[event,processing,event] as any;
  const next=advanceEvents(.99,40,1,events);
  assert.ok(Math.abs(next-(1+29/700))<1e-10);
  assert.equal(advanceEvents(1,0,2,events),1);
  assert.equal(advanceEvents(2.99,100,4,events),3);
  assert.equal(advanceEvents(0,10000,1,events),100/1100);
});
