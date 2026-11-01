// Test the real UI script's fetch/clock state machine with a minimal DOM host.
// No browser dependencies, no database writes, no arbitrary sleeps.
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const fixture=JSON.parse(fs.readFileSync(path.join(root,'evidence/operations_snapshot_example.json'),'utf8'));
let now=fixture.meta.generated_at*1000+100,offline=false;
fixture.meta.collector_last_seen=now/1000;
const timers=[],elements=new Map();
function element(id){
 if(elements.has(id))return elements.get(id);
 const classes=new Set();
 const e={id,style:{},textContent:'',innerHTML:'',hidden:false,value:id==='branch'?'all':id==='period'?'7d':'',
  classList:{contains:n=>classes.has(n),toggle(n,v){v=v??!classes.has(n);v?classes.add(n):classes.delete(n);return v;}},
  querySelectorAll:()=>[],replaceChildren(){},append(){},focus(){},getClientRects:()=>[{}]};
 elements.set(id,e);return e;
}
class Clock extends Date{constructor(...args){super(...(args.length?args:[now]));}static now(){return now;}}
const sandbox={console,Date:Clock,URLSearchParams,AbortController,innerWidth:1920,innerHeight:1080,
 document:{getElementById:element,querySelectorAll:()=>[],addEventListener(){},body:element('body'),createElement:()=>element('new')},
 addEventListener(){},location:{assign(){}},setTimeout(){return 1;},clearTimeout(){},
 setInterval(fn,ms){timers.push({fn,ms});return timers.length;},
 echarts:{init:()=>({setOption(){},off(){},on(){}}),graphic:{LinearGradient:function(){}}},
 async fetch(url){return {ok:!offline,json:async()=>url==='/api/health'?{csrf:'test-only'}:offline?{error:'database unavailable'}:structuredClone(fixture)};},window:{}};
vm.runInNewContext(fs.readFileSync(path.join(root,'frontend/operations.js'),'utf8'),sandbox);
await new Promise(setImmediate);
assert.equal(element('connection').textContent,'实时连接');
offline=true;now+=1000;
await sandbox.window.OperationsUI.refresh();
assert.equal(element('connection').textContent,'连接中断');
timers.find(t=>t.ms===1000).fn();
assert.equal(element('connection').textContent,'连接中断','clock must never turn a failed fetch green');
offline=false;now+=1000;fixture.meta.generated_at=now/1000;fixture.meta.collector_last_seen=now/1000;
await sandbox.window.OperationsUI.refresh();
assert.equal(element('connection').textContent,'实时连接');
const report={suite:'operations_ui_connection_state',date:'2026-11-01',run:4,passed:4,
 checks:['successful snapshot is live','fetch error shows disconnected','clock preserves failed-fetch state','recovered snapshot restores live state']};
fs.writeFileSync(path.join(root,'evidence/ops_ui_state_results.json'),JSON.stringify(report,null,2));
console.log(JSON.stringify(report));
