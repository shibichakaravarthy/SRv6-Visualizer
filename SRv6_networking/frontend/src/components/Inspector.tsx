import {memo, useState} from 'react';
import {ConfigEditor} from './ConfigEditor';
import type {Device, Event, Json, View} from '../types';
export function JsonDetails({value}: {value: unknown}) {return <pre>{JSON.stringify(value, null, 2)}</pre>;}
function Observation({name, value}: {name: string; value: Json}) {
  return <details><summary>{name} · {value.status || 'collected'}</summary>{value.error && <p className="error">{value.error}</p>}<JsonDetails value={value.data ?? value.raw ?? value}/></details>;
}
export const Inspector = memo(function Inspector({device, view, event, liveView, onApplied, disabled}: {device?: Device; view: View; event?: Event; liveView: View; onApplied:(v:View)=>void; disabled:boolean}) {
  const [tab, setTab] = useState('Interfaces');
  const tabs = ['Interfaces', 'Routes', 'IS-IS', 'SRv6', 'Config', 'Intent', 'Packet', 'Edit'];
  const record = (name: string) => view.snapshots[name]?.records.find((r: Json) => r.id === device?.id);
  return <aside className="inspector"><div className="panel-title"><span>DEVICE INSPECTOR</span><h2>{device?.name || 'Select a device'}</h2><small>{device ? `${device.role} · ${device.state}` : 'Click any node in the workspace'}</small></div>
    <nav>{tabs.map(t => <button key={t} className={tab === t ? 'chosen' : ''} onClick={() => setTab(t)}>{t}</button>)}</nav>
    <div className="inspector-body">
      {tab === 'Packet' ? event ? <><h3>{event.description}</h3><PacketSummary event={event}/><p>Observed capture timestamps; playback is slowed for inspection.</p><p>Before: {event.before.name}:{event.before.interface} · frame {event.before.frame}</p><p>After: {event.after.name}:{event.after.interface} · frame {event.after.frame}</p><Observation name="Incoming packet headers" value={event.before.state}/><Observation name="Outgoing packet headers" value={event.after.state}/>{event.fields && <Observation name="Comparable field changes" value={event.fields}/>}<Observation name="Provenance and installed context" value={event}/></> : <p>Run a ping and select a captured packet.</p> : !device ? <p>Select a device to inspect its collected state.</p> : <>
        {tab === 'Edit' && <ConfigEditor key={device.id} device={liveView.nodes.find(n=>n.id===device.id) || device} view={liveView} onApplied={onApplied} disabled={disabled}/>}
        {tab === 'Interfaces' && <><p>Collected addresses and live interface state. Management addresses are excluded from ping selection.</p><JsonDetails value={device.addresses}/><Observation name="IPv6 interfaces" value={device.ipv6_addresses}/><Observation name="Link state" value={device.link_observation}/></>}
        {tab === 'Routes' && Object.entries(record('ipv6_routes') || {}).filter(([k]) => ['routes', 'neighbors', 'rules'].includes(k)).map(([k, v]) => <Observation key={k} name={k} value={v as Json}/>)}
        {tab === 'IS-IS' && <><Observation name="Adjacencies" value={device.isis}/><Observation name="IS-IS routes" value={device.isis_routes}/></>}
        {tab === 'SRv6' && <><p>Installed state at collection time. A segment list specifies instructions, not every physical hop.</p><JsonDetails value={record('srv6_policies') || {status: 'missing'}}/></>}
        {tab === 'Config' && Object.entries(device.configuration || {}).map(([k, v]) => <Observation key={k} name={k} value={v as Json}/>)}
        {tab === 'Intent' && <><p>Declared configuration files. These are separate from installed state and observed execution.</p>{(view.snapshots.configurations?.configured_files.files || []).map((f: Json) => <details key={f.path}><summary>{f.path.split('/').slice(-2).join('/')}</summary><pre>{f.content || f.error}</pre></details>)}</>}
      </>}
    </div></aside>;
});

function PacketSummary({event}: {event: Event}) {
  const before = event.before.state, after = event.after.state;
  const inner = (after.encapsulation?.layers || []).at(-1) || after.ipv6;
  return <div className="packet-summary"><b>ICMPv6 Echo {event.before.packet_key?.[0] === 129 ? 'Reply' : 'Request'}</b>
    <dl><dt>Inner source</dt><dd>{inner?.source}</dd><dt>Inner destination</dt><dd>{inner?.destination}</dd>
      <dt>Outer destination</dt><dd>{before.ipv6?.destination} → {after.ipv6?.destination}</dd>
      <dt>Segments left</dt><dd>{before.srh_present ? before.srh?.segments_left : 'SRH absent'} → {after.srh_present ? after.srh?.segments_left : 'SRH absent'}</dd>
      <dt>Segment list (wire order)</dt><dd>{after.srh?.segment_list?.join(' → ') || 'No SRH in outgoing capture'}</dd>
      <dt>Installed SID match</dt><dd>{event.matched_sid ? `${event.matched_sid.sid} · ${event.matched_sid.action} (inferred execution)` : 'No specific SID action established'}</dd></dl>
  </div>;
}
