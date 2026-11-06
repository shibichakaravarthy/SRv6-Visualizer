/** Small, original network equipment illustrations; no external icon assets. */
export function deviceKind(name: string) {
  return /firewall|ids/i.test(name) ? 'security' : /server/i.test(name) ? 'server' : /client|host|pc/i.test(name) ? 'pc' : /^sw/i.test(name) ? 'switch' : 'router';
}
export function DeviceIcon({name, className = ''}: {name: string; className?: string}) {
  const kind = deviceKind(name);
  return <svg className={`equipment-icon ${className}`} viewBox="0 0 72 60" fill="none" role="img" aria-label={`${kind} device`}>
    {kind === 'router' && <>
      <ellipse cx="36" cy="48" rx="29" ry="5" fill="#dbe7ed"/>
      <path d="M7 23v19c0 13 58 13 58 0V23" fill="#1680b4" stroke="#165875" strokeWidth="1.5"/>
      <ellipse cx="36" cy="23" rx="29" ry="14" fill="#55b7df" stroke="#165875" strokeWidth="1.5"/>
      <path d="m18 18 11 1-4-4m4 4-9 6m34-5-11-1 4 4m-4-4 9-6M30 29l6-5 6 5m-6-5v10M30 13l6 5 6-5m-6 5V9" stroke="white" strokeWidth="2.3" strokeLinejoin="round"/>
      <path d="M14 40c12 7 32 7 44 0" stroke="#86cde6" strokeWidth="1.3"/>
      <circle cx="54" cy="42" r="2" fill="#c4fa93"/>
    </>}
    {kind === 'switch' && <>
      <path d="m5 25 13-13h43l7 13v22H5Z" fill="#1588b8" stroke="#145e7a" strokeWidth="1.5"/>
      <path d="M5 25h63L61 12H18Z" fill="#61c2df" stroke="#145e7a" strokeWidth="1.5"/>
      <path d="M17 19h15m-4-3 4 3-4 3m26-3H39m4-3-4 3 4 3" stroke="white" strokeWidth="2" strokeLinejoin="round"/>
      {[12,23,34,45].map(x => <g key={x}><rect x={x} y="32" width="8" height="7" rx="1" fill="#123e54"/><path d={`M${x+2} 33v2m2-2v2m2-2v2`} stroke="#b5d6df"/></g>)}
      <circle cx="60" cy="35" r="2" fill="#bded8a"/>
    </>}
    {kind === 'pc' && <>
      <rect x="49" y="10" width="17" height="39" rx="3" fill="#3c5367" stroke="#263746" strokeWidth="1.5"/>
      <path d="M53 16h9m-9 5h9" stroke="#a1b7c6" strokeWidth="2"/><circle cx="57" cy="42" r="2" fill="#87d8ac"/>
      <path d="M24 40v10m-10 3h30" stroke="#445a6b" strokeWidth="4"/>
      <rect x="5" y="14" width="43" height="29" rx="3" fill="#455d71" stroke="#263746" strokeWidth="1.5"/>
      <path d="M9 18h35v20H9Z" fill="#84cbed"/><path d="M9 18h35L9 38Z" fill="#b1e4f7"/>
    </>}
    {kind === 'server' && <>
      <path d="m15 12 7-7h30l6 7v41H15Z" fill="#344e62" stroke="#223e50" strokeWidth="1.5"/>
      {[14,27,40].map(y => <g key={y}><rect x="19" y={y} width="35" height="10" rx="2" fill="#547488" stroke="#203f50"/><circle cx="24" cy={y+5} r="2" fill="#90e5b0"/><path d={`M32 ${y+4}h16m-16 3h16`} stroke="#bdd6e1" strokeWidth="1.4"/></g>)}
    </>}
    {kind === 'security' && <>
      <path d="M10 15h52v37H10Z" fill="#da8c54" stroke="#925632" strokeWidth="1.5"/><path d="M10 27h52M10 39h52M23 15v12m25-12v12M35 27v12M23 39v13m25-13v13" stroke="#ffe0c1" strokeWidth="2"/>
      <path d="m36 5 15 6v11c0 10-15 18-15 18s-15-8-15-18V11Z" fill="#427e98" stroke="white" strokeWidth="2"/><path d="m29 21 5 5 10-12" stroke="white" strokeWidth="3"/>
    </>}
  </svg>;
}
