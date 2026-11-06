import {useCallback, useEffect, useRef, useState} from 'react';
import {Activity, RefreshCw, Play, Pause, RotateCcw, Network} from 'lucide-react';
import {api} from './api';
import {Topology} from './components/Topology';
import {Inspector} from './components/Inspector';
import {usePlayback} from './usePlayback';
import type {Job, Json, View} from './types';
const empty: View = {nodes: [], links: [], snapshots: {}, collection_id: '', collected_at: ''};
export default function App() {
  const [view, setView] = useState<View>(empty), [selected, setSelected] = useState(''), [error, setError] = useState('');
  const [source, setSource] = useState(''), [destination, setDestination] = useState(''), [sourceAddress, setSourceAddress] = useState(''), [destinationAddress, setDestinationAddress] = useState('');
  const [job, setJob] = useState<Job>({state: 'idle', journeys: [], errors: []}), [packet, setPacket] = useState(0), [busy, setBusy] = useState(false), [auto, setAuto] = useState(false), [status, setStatus] = useState<Json>({});
  const [runView, setRunView] = useState<View>();
  const [replay, setReplay] = useState(true);
  const [preparing, setPreparing] = useState(false);
  const journey = replay ? job.journeys?.[packet] : undefined, events = journey?.events || [];
  const playback = usePlayback(`${job.id}:${packet}:${replay}`, events), event = events[playback.index];
  const timeline = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const panel = timeline.current, row = panel?.querySelector('.current');
    if (!panel || !row) return;
    const bounds = panel.getBoundingClientRect(), current = row.getBoundingClientRect();
    const offset = current.top < bounds.top ? current.top - bounds.top - 8 : current.bottom > bounds.bottom ? current.bottom - bounds.bottom + 8 : 0;
    if (offset) panel.scrollBy({top:offset, behavior:'smooth'});
  }, [playback.index, job.id, packet, replay]);
  const clients = view.nodes.filter(n => ['host', 'client', 'server'].includes(n.role) && n.running && n.addresses.length);
  const refresh = async () => {try {setError(''); await api('refresh', {}); setBusy(true); setReplay(false); playback.reset();} catch (e) {setError(String(e));}};
  useEffect(() => {api<View>('workspace').then(setView).catch(e => setError(String(e))); Promise.all([api<Job>('pings/current'), api<View | null>('pings/current/workspace')]).then(([saved, snapshot]) => {setJob(saved); if (snapshot) setRunView(snapshot);}).catch(e => setError(String(e)));}, []);
  useEffect(() => {
    if (!busy && job.state !== 'running') return;
    let cancelled = false;
    const timer = setInterval(async () => {
      try {
        const next = await api<Json>('status'); if (cancelled) return; setStatus(next);
        if (busy && next.collection.state !== 'running') {setView(await api<View>('workspace')); setBusy(false);}
        if (job.state === 'running' && next.ping.state !== 'running') {setJob(await api<Job>('pings/current')); setPacket(0);}
      } catch (e) {if (!cancelled) {setError(String(e)); setBusy(false);}}
    }, 1500);
    return () => {cancelled = true; clearInterval(timer);};
  }, [busy, job.state]);
  useEffect(() => {if (!auto) return; const timer = setInterval(() => {if (!preparing && !busy && job.state !== 'running' && !playback.playing && !document.hidden) void refresh();}, 60000); return () => clearInterval(timer);}, [auto, preparing, busy, job.state, playback.playing]);
  useEffect(() => {setSourceAddress(view.nodes.find(n => n.id === source)?.addresses[0]?.address || '');}, [source, view]);
  useEffect(() => {setDestinationAddress(view.nodes.find(n => n.id === destination)?.addresses[0]?.address || '');}, [destination, view]);
  async function execute() {
    setPreparing(true);
    try {
      setError(''); playback.reset(); setReplay(true);
      const next = await api<Job>('pings', {source, destination, source_address: sourceAddress, destination_address: destinationAddress});
      setJob(next); setPacket(0);
      const snapshot = await api<View>('pings/current/workspace');
      setRunView(snapshot); setView(snapshot);
    } catch (e) {setError(String(e));}
    finally {setPreparing(false);}
  }
  const onApplied = useCallback((next: View) => {setView(next);setReplay(false);playback.reset();}, [playback.reset]);
  // Completed evidence is always shown against its original topology generation.
  const shown = journey && runView ? runView : view;
  return <div className="app"><header><div className="brand"><Network size={28}/><div><h1>SRv6 Workspace</h1><span>CONTAINERLAB · PACKET EVIDENCE EXPLORER</span></div></div><div className="header-right"><label><input type="checkbox" checked={auto} onChange={e => setAuto(e.target.checked)}/> Refresh every minute</label><button disabled={preparing || busy || job.state === 'running'} onClick={refresh}><RefreshCw size={15} className={busy ? 'spin' : ''}/>{busy ? 'Collecting…' : 'Refresh topology'}</button></div></header>
    <div className="statusline"><span><i className={view.nodes.length ? 'up' : 'down'}/> {shown.collected_at ? `${journey ? 'Replay snapshot' : 'Snapshot'} ${new Date(shown.collected_at).toLocaleString()}` : 'No collection yet'}</span><span>{busy ? `${status.collection?.completed_nodes || 0} / ${status.collection?.total_nodes || '?'} devices` : view.snapshots.topology?.status || 'Awaiting discovery'} · {view.nodes.filter(n => n.running).length} running</span><span>Intent → Installed state → Observed execution</span></div>
    {error && <div role="alert" className="error banner">{error}</div>}
    {(view.snapshots.topology?.errors?.length > 0) && <details className="collection-errors"><summary>Collection limitations ({view.snapshots.topology.errors.length})</summary>{view.snapshots.topology.errors.map((s: string, i: number) => <p key={i}>{s}</p>)}</details>}
    <main><Topology devices={shown.nodes} links={shown.links} selected={selected} onSelect={setSelected} event={event} progress={playback.progress} direction={journey?.direction || ''} clock={playback.clock} index={playback.index}/><Inspector device={shown.nodes.find(n => n.id === selected)} view={shown} event={event} liveView={view} disabled={preparing || busy || job.state === 'running'} onApplied={onApplied}/></main>
    <section className="simulation"><div className="simulation-title"><Activity size={18}/><h2>Client-to-client ping</h2><span>Real execution · Evidence replay</span></div>
      <div className="execution-controls"><label>Source client<select aria-label="Source client" value={source} onChange={e => setSource(e.target.value)}><option value="">Select source</option>{clients.map(n => <option key={n.id} value={n.id}>{n.name}</option>)}</select></label><label>Source IPv6<select aria-label="Source IPv6" value={sourceAddress} onChange={e => setSourceAddress(e.target.value)}><option value="">Select address</option>{view.nodes.find(n => n.id === source)?.addresses.map(a => <option key={a.address} value={a.address}>{a.address} · {a.interface}</option>)}</select></label><span className="arrow">→</span><label>Destination client<select aria-label="Destination client" value={destination} onChange={e => setDestination(e.target.value)}><option value="">Select destination</option>{clients.filter(n => n.id !== source).map(n => <option key={n.id} value={n.id}>{n.name}</option>)}</select></label><label>Destination IPv6<select aria-label="Destination IPv6" value={destinationAddress} onChange={e => setDestinationAddress(e.target.value)}><option value="">Select address</option>{view.nodes.find(n => n.id === destination)?.addresses.map(a => <option key={a.address} value={a.address}>{a.address} · {a.interface}</option>)}</select></label><button className="primary" disabled={preparing || busy || job.state === 'running' || !sourceAddress || !destinationAddress || source === destination} onClick={execute}><Play size={15}/>{preparing ? 'Checking lab…' : job.state === 'running' ? `Running · ${status.ping?.phase || 'capture'}` : 'Run ping'}</button></div>
      <div className="playback"><button disabled={!job.journeys?.length} onClick={() => setReplay(v => !v)}>{replay ? 'Live topology' : 'Saved replay'}</button><select aria-label="Captured packet" value={packet} onChange={e => {setPacket(Number(e.target.value)); setReplay(true);}}><option disabled value={-1}>Captured packets</option>{job.journeys?.map((j, i) => <option key={j.packet_id} value={i}>Echo {j.direction} #{j.sequence} · {j.complete ? 'complete' : 'partial'} coverage</option>)}</select><button disabled={!events.length} onClick={playback.playing ? playback.pause : playback.play}>{playback.playing ? <Pause size={14}/> : <Play size={14}/>} {playback.playing ? 'Pause' : playback.position > 0 ? 'Resume / replay' : 'Play'}</button><button disabled={!events.length} onClick={playback.reset}><RotateCcw size={14}/>Reset</button><label>Speed<select value={playback.speed} onChange={e => playback.setSpeed(Number(e.target.value))}>{[.25, .5, 1, 2, 4].map(s => <option key={s} value={s}>{s}×</option>)}</select></label><progress value={playback.position} max={events.length || 1}/><span>{events.length ? `${playback.index + 1}/${events.length} · ${event?.description}` : 'No packet movement without capture evidence'}</span></div>
      <div className="evidence-bottom"><div ref={timeline} className="timeline"><h3>Event timeline <small>Slowed replay; timestamps are original observations</small></h3>{journey?.issues.map((s, i) => <p key={i} className="notice">{s}</p>)}{events.map((e, i) => <button key={i} className={i === playback.index ? 'current' : ''} onClick={() => playback.seek(i)}><time>{new Date(e.timestamp * 1000).toISOString().slice(11, 23)}</time><b>{i + 1}</b><span>{e.description}</span><small>observed</small></button>)}{!events.length && <p>Start a ping to collect request and reply evidence across the lab. Missing captures will be reported here.</p>}</div><div className="results"><h3>Ping result · {job.state}</h3>{job.statistics && <p>{job.statistics.received ?? '?'} / {job.statistics.transmitted ?? '?'} received · {job.statistics.loss_percent ?? '?'}% loss · average {job.statistics.rtt_ms?.avg ?? 'unavailable'} ms</p>}{job.errors?.map((s, i) => <p className="notice" key={i}>{s}</p>)}{job.ping && <pre>{job.ping.raw}{job.ping.error}</pre>}<p className="muted">Replay uses paired captures on verified physical links. SID actions are inferred only where observed header changes and installed state support them. Gaps are never filled with guessed routes.</p></div></div>
    </section><footer>SRv6 research workspace · Local lab access · Preserved policy and experiment workflow</footer></div>;
}
