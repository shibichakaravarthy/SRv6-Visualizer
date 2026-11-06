import {useState} from 'react';
import {api} from '../api';
import type {Device, Json, View} from '../types';

const initial = {kind:'policy',operation:'replace',prefix:'',interface:'',table:254,metric:1024,gateway:'',segments:[] as string[],mode:'encap',action:'End',lookup_table:254,next_csid:false,lblen:32,nflen:16,commands:''};
export function ConfigEditor({device, view, onApplied, disabled}: {device:Device; view:View; onApplied:(v:View)=>void; disabled:boolean}) {
  const [form,setForm]=useState({...initial,interface:device.interfaces?.find((i:Json)=>i.ifname!=='eth0' && i.ifname!=='lo')?.ifname || ''});
  const [segments,setSegments]=useState(''), [preview,setPreview]=useState<Json>(), [result,setResult]=useState<Json>(), [error,setError]=useState(''), [working,setWorking]=useState(false);
  const routes:Json[]=view.snapshots.ipv6_routes?.records.find((r:Json)=>r.id===device.id)?.routes.data || [];
  const editable=routes.filter(r=>(!r.type || r.type==='unicast') && (!r.encap || (['seg6','seg6local'].includes(r.encap.encap_type || r.encap.type) && (!r.encap.action || ['End','End.X','End.DX6','End.DT6'].includes(r.encap.action)))) && [undefined,'boot','static',3,4].includes(r.protocol) && r.dev!=='eth0' && !r.nhid && !r.multipath && !['local',255,'255'].includes(r.table));
  const update=(key:string,value:unknown)=>{setForm(f=>({...f,[key]:value,...(key==='kind'?{operation:'replace',next_csid:false}:{}),...(key==='action'&&value!=='End'?{next_csid:false}:{})}));setPreview(undefined);setResult(undefined);setError('');};
  const load=(index:string)=>{
    setPreview(undefined);setResult(undefined);setError('');
    if(index===''){setForm({...initial,interface:form.interface});setSegments('');return;}
    const r=editable[Number(index)], e=r.encap || {}, kind=e.encap_type || e.type;
    const flavor=e.flavors || {};
    setForm({...initial,kind:kind==='seg6'?'policy':kind==='seg6local'?'sid':'ipv6',prefix:r.dst==='default'?'::/0':r.dst,interface:r.dev || '',table:r.table==='main'||!r.table?254:Number(r.table),metric:r.metric || 1024,gateway:e.nh6 || r.gateway || '',mode:e.mode || 'encap',action:e.action || 'End',lookup_table:Number(e.table || 254),next_csid:JSON.stringify(flavor).includes('next-csid'),lblen:flavor.lblen || e.lblen || 32,nflen:flavor.nflen || e.nflen || 16});
    setSegments((e.segs || []).join(', '));
  };
  async function submit(apply:boolean){
    setWorking(true);setError('');
    try {
      const body={...form,device:device.id,segments:form.kind==='policy'?segments.split(/[\s,]+/).filter(Boolean):[],expected:apply?preview?.expected:undefined};
      const data=await api<Json>(`config/${apply?'apply':'preview'}`,body);
      if(apply){setResult(data);setPreview(undefined);onApplied(data.workspace);}else setPreview(data);
    }catch(e){setError(String(e));setPreview(undefined);}finally{setWorking(false);}
  }
  function download(){
    const command=preview?.command_text || result?.command_text;
    const blob=new Blob([`#!/bin/sh\n# Run inside ${device.name}; runtime configuration exported from SRv6 Workspace.\nset -e\n${command}\n`],{type:'text/plain'});
    const url=URL.createObjectURL(blob), a=document.createElement('a');a.href=url;a.download=`${device.name}-configuration.sh`;a.click();URL.revokeObjectURL(url);
  }
  if(device.role!=='router')return <p>Select a router to edit routing and FRR settings.</p>;
  return <div className="config-editor"><h3>Configure {device.name}</h3><p>Changes apply to the running router. Lab deployment files stay separate. Download the reviewed command to retain it for redeployment.</p>
    <fieldset disabled={working || disabled}>
      <label>Load installed route<select aria-label="Load installed route" defaultValue="" onChange={e=>load(e.target.value)}><option value="">New entry</option>{editable.map((r,i)=><option key={i} value={i}>{r.dst} · {r.encap?.action || r.encap?.encap_type || 'IPv6'} · table {r.table || 'main'} · metric {r.metric}</option>)}</select></label>
      <label>Configuration type<select value={form.kind} onChange={e=>{update('kind',e.target.value);setPreview(undefined);}}><option value="policy">SRv6 steering policy</option><option value="sid">Local SID</option><option value="ipv6">Normal IPv6 route</option><option value="frr">FRR IPv6 / IS-IS configuration</option></select></label>
      {form.kind==='frr'?<><p>Supported: interface IPv6 addresses, IPv6 IS-IS membership, IS-IS metric/passive/circuit-type; router isis NET, is-type and metric-style. Use exit between contexts. Configuration is runtime-only and multi-line edits can partially apply.</p><label>FRR commands<textarea rows={10} value={form.commands} onChange={e=>update('commands',e.target.value)} placeholder={'interface eth1\n isis metric 20\nexit'}/></label></>:<>
        <label>Operation<select value={form.operation} onChange={e=>update('operation',e.target.value)}><option value="replace">Add / replace</option><option value="delete">Delete selected route</option></select></label>
        <label>{form.kind==='sid'?'SID prefix':'Destination prefix'}<input aria-label="Route prefix" value={form.prefix} onChange={e=>update('prefix',e.target.value)} placeholder="IPv6 address or prefix"/></label>
        <div className="form-pair"><label>Route table<input type="number" min="1" value={form.table} onChange={e=>update('table',Number(e.target.value))}/></label><label>Metric<input type="number" min="1" value={form.metric} onChange={e=>update('metric',Number(e.target.value))}/></label></div>
        <small>Editing uses prefix + table + metric. Changing these creates a different entry. FRR-owned and connected routes are read-only here.</small>
        {form.operation!=='delete'&&<>
          <label>Output interface<select value={form.interface} onChange={e=>update('interface',e.target.value)}>{device.interfaces?.filter((i:Json)=>i.ifname!=='eth0').map((i:Json)=><option key={i.ifname}>{i.ifname}</option>)}</select></label>
          {form.kind==='policy'&&<><label>Mode<select value={form.mode} onChange={e=>update('mode',e.target.value)}>{['encap','encap.red','inline'].map(m=><option key={m}>{m}</option>)}</select></label><label>SIDs in visit order<textarea rows={4} value={segments} onChange={e=>{setSegments(e.target.value);setPreview(undefined);}} placeholder="Comma-separated IPv6 SIDs"/></label><small>Intermediate transit routers are discovered from captures, not this list.</small></>}
          {form.kind==='sid'&&<><label>SID action<select value={form.action} onChange={e=>update('action',e.target.value)}>{['End','End.X','End.DX6','End.DT6'].map(a=><option key={a}>{a}</option>)}</select></label>{form.action==='End.DT6'&&<label>Lookup table<input type="number" min="1" value={form.lookup_table} onChange={e=>update('lookup_table',Number(e.target.value))}/></label>}{form.action==='End'&&<><label className="inline"><input type="checkbox" checked={form.next_csid} onChange={e=>update('next_csid',e.target.checked)}/> NEXT-CSID flavor</label>{form.next_csid&&<div className="form-pair"><label>Block length<input type="number" step="8" value={form.lblen} onChange={e=>update('lblen',Number(e.target.value))}/></label><label>Node length<input type="number" step="8" value={form.nflen} onChange={e=>update('nflen',Number(e.target.value))}/></label></div>}</>}</>}
          {(form.kind==='ipv6'||(form.kind==='sid'&&['End.X','End.DX6'].includes(form.action)))&&<label>IPv6 next hop<input value={form.gateway} onChange={e=>update('gateway',e.target.value)} placeholder={form.kind==='ipv6'?'Optional for an on-link route':'Required next hop'}/></label>}
        </>}
      </>}
      <button onClick={()=>submit(false)}>{working?'Working…':'Preview change'}</button>
    </fieldset>
    {error&&<p className="error" role="alert">{error}</p>}
    {preview&&<div className="change-preview"><h3>Review command</h3><pre>{preview.command_text}</pre><details><summary>Existing configuration affected</summary><pre>{JSON.stringify(preview.before,null,2)}</pre></details><button disabled={working || disabled} className="primary" onClick={()=>submit(true)}>Apply to router</button><button onClick={download}>Download command</button></div>}
    {result&&<div role="status"><p className={result.status==='failed'?'error':'notice'}>{result.message}</p><pre>{result.result?.raw}{result.result?.error}</pre><small>Audit: {result.audit_file}</small><details><summary>Collected state after change</summary><pre>{JSON.stringify(result.after?.data ?? result.after?.raw,null,2)}</pre></details><button onClick={download}>Download command</button></div>}
  </div>;
}
