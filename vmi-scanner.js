(function(root){'use strict';
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const done=r=>['REJECTED','QUALIFIED'].includes(r.status);
// Lossless audit export: identical objects, calculation inputs and long source
// strings are stored once. References are local node indices, never URLs.
function encodeExport(content){
 const nodes=[],intern=new Map(),seen=new WeakMap();
 function encode(value){
  if(typeof value==='string'&&value.length>80){
   const key=JSON.stringify(value);let id=intern.get(key);
   if(id===undefined){id=nodes.length;nodes.push(value);intern.set(key,id);}
   return {$ref:id};
  }
  if(value===null||typeof value!=='object')return value;
  if(seen.has(value))return seen.get(value);
  const node=Array.isArray(value)?value.map(encode):Object.fromEntries(Object.keys(value).sort().filter(k=>value[k]!==undefined).map(k=>[k,encode(value[k])]));
  const key=JSON.stringify(node);let id=intern.get(key);
  if(id===undefined){id=nodes.length;nodes.push(node);intern.set(key,id);}
  const ref={$ref:id};seen.set(value,ref);return ref;
 }
 const root=encode(content);
 return {kind:'paper-desk-vmi-sp500',version:2,encoding:'json-dag-v1',root,nodes};
}
function decodeExport(content){
 if(content?.kind!=='paper-desk-vmi-sp500')throw Error('Not a Paper Desk scan');
 if(content.version===1)return content;
 if(content.version!==2||content.encoding!=='json-dag-v1'||!Array.isArray(content.nodes))throw Error('Unsupported scan export');
 const memo=new Map(),active=new Set();
 function decode(ref){
  if(ref===null||typeof ref!=='object')return ref;
  const id=ref.$ref;
  if(Object.keys(ref).length!==1||!Number.isInteger(id)||id<0||id>=content.nodes.length)throw Error('Invalid audit reference');
  if(active.has(id))throw Error('Cyclic audit reference');
  if(memo.has(id))return memo.get(id);
  active.add(id);const node=content.nodes[id];
  if(typeof node==='string'){active.delete(id);memo.set(id,node);return node;}
  if(node===null||typeof node!=='object')throw Error('Invalid audit node');
  const value=Array.isArray(node)?node.map(decode):Object.fromEntries(Object.entries(node).map(([k,v])=>[k,decode(v)]));
  active.delete(id);memo.set(id,value);return value;
 }
 return decode(content.root);
}
function create(adapter){
 let run=0,controller=null,busy=false,rows=new Map(),phase='Ready',progress='',preset='',startedAt=null,finishedAt=null,universe=null,filter='qualified',node=null,error='';
 let sequence=0,runToken='',stageWork=null,stages=[],log=[],active=new Map(),traces=new Map(),counts=new Map(),totals={},lastDone=null,heartbeat=null,polling=false,lastPoll=0,diagnosticError='',cooldowns={};
 const seconds=ms=>Math.max(0,Math.floor(ms/1000))+'s';
 function note(text){log.push({at:Date.now(),text:String(text)});if(log.length>150)log.shift();}
 function beginStage(name,total){stageWork={name,total,done:0,excluded:0,missing:0,started:Date.now(),finished:null};stages.push(stageWork);phase=name;progress='0 / '+total;note(name+' started: '+total+' listings');render();}
 function endStage(){stageWork.finished=Date.now();note(stageWork.name+' finished: '+stageWork.done+' visited, '+stageWork.excluded+' excluded, '+stageWork.missing+' requiring further acquisition');}
 function consumeDiagnostics(d){for(const row of d?.requests||[]){const previous=counts.get(row.id)||{};for(const [k,v] of Object.entries(row.counts||{}))totals[k]=(totals[k]||0)+Math.max(0,v-(previous[k]||0));counts.set(row.id,row.counts||{});traces.set(row.id,row);if(traces.size>40)traces.delete(traces.keys().next().value);}if(d?.cooldowns)cooldowns=d.cooldowns;}
 const stats=()=>{const values=[...rows.values()];return {total:values.length,assessed:values.filter(done).length,qualified:values.filter(r=>r.status==='QUALIFIED').length,opportunities:values.filter(r=>r.status==='QUALIFIED'&&r.undervalued).length,rejected:values.filter(r=>r.status==='REJECTED').length,acquiring:values.filter(r=>!done(r)).length};};
 function stale(id){return id!==run||controller?.signal.aborted;}
 async function request(path,id,label='Scanner request'){
  if(stale(id))throw new DOMException('Cancelled','AbortError');
  const work=runToken+'-'+(++sequence),job={id:work,label,started:Date.now()};active.set(work,job);note('Started '+label);updateDebug();
  const timeout=new AbortController(),duration=path.includes('_batch?')?30000:150000,timer=setTimeout(()=>timeout.abort(),duration);
  const owner=controller,abort=()=>timeout.abort();owner.signal.addEventListener('abort',abort,{once:true});
  try{
   const response=await fetch(path+(path.includes('?')?'&':'?')+'work='+encodeURIComponent(work),{signal:timeout.signal});
   if(!response.ok)throw Error('Data HTTP '+response.status);const data=await response.json();if(data.error)throw Error(data.error);
   if(stale(id))throw new DOMException('Cancelled','AbortError');consumeDiagnostics(data.diagnostics);
   note('Finished '+label+' in '+seconds(Date.now()-job.started));lastDone=Date.now();return data;
  }catch(e){if(!stale(id))note('Request failed '+label+': '+(timeout.signal.aborted?'Timed out after '+duration/1000+'s; not a pass':e.message));throw e;}
  finally{clearTimeout(timer);owner.signal.removeEventListener('abort',abort);if(id===run){active.delete(work);updateDebug();}}
 }
 function debugHTML(){
  const now=finishedAt||Date.now(),idle=lastDone?Date.now()-lastDone:startedAt?Date.now()-startedAt:0;
  const jobs=[...active.values()];
  return '<h3>Live scanner diagnostics</h3><p>Elapsed '+seconds(startedAt?now-startedAt:0)+' · '+jobs.length+' active requests · last completion '+(lastDone?seconds(Date.now()-lastDone)+' ago':'not yet')+'</p>'+(busy&&idle>20000?'<p class="vmi-scan-error">No request has finished for '+seconds(idle)+'. Active server operations are listed below; this is not additional screening progress.</p>':'')+(diagnosticError?'<p class="vmi-scan-error">'+esc(diagnosticError)+'</p>':'')+'<p>Cache hits '+(totals.cache_hit||0)+' · shared waits '+(totals.cache_wait||0)+' · HTTP downloads '+(totals.download||0)+' · retries '+(totals.retry||0)+' · upstream errors '+((totals.http_error||0)+(totals.network_error||0))+'</p><p>Provider cooldowns: Finviz '+(cooldowns.finvizSeconds||0)+'s; Yahoo public pages '+(cooldowns.yahooSeconds||0)+'s. SEC/statement sources remain independent.</p><ul>'+jobs.map(j=>{const t=traces.get(j.id);return '<li><strong>'+esc(j.label)+'</strong> · '+seconds(Date.now()-j.started)+' · '+esc(t?[t.phase,t.provider,t.detail].filter(Boolean).join(' / '):'Waiting for server telemetry')+(t?.events?.at(-1)?.waitSeconds?' · reported wait '+esc(t.events.at(-1).waitSeconds)+'s':'')+'</li>';}).join('')+'</ul><div class="vmi-scroll"><table><thead><tr><th>Stage</th><th>Visited / planned</th><th>Excluded</th><th>Further acquisition</th><th>Time</th></tr></thead><tbody>'+stages.map(s=>'<tr><td>'+esc(s.name)+'</td><td>'+s.done+' / '+s.total+'</td><td>'+s.excluded+'</td><td>'+s.missing+'</td><td>'+seconds((s.finished||Date.now())-s.started)+'</td></tr>').join('')+'</tbody></table></div><p>Visited is work attempted, not business qualification. Missing data never passes. Only qualified businesses appear in results.</p><h4>Recent activity</h4><ol>'+log.slice(-10).reverse().map(e=>'<li>'+esc(new Date(e.at).toLocaleTimeString()+' · '+e.text)+'</li>').join('')+'</ol>';
 }
 function updateDebug(){const target=node?.querySelector?.('#vmi-scan-debug');if(target)target.innerHTML=debugHTML();}
 async function pollDiagnostics(id){
  if(polling||stale(id)||!busy)return;const ids=[...active.keys()];if(!ids.length)return;
  polling=true;lastPoll=Date.now();const stop=new AbortController(),timer=setTimeout(()=>stop.abort(),4500),owner=controller,abort=()=>stop.abort();owner.signal.addEventListener('abort',abort,{once:true});
  try{const r=await fetch('/data/vmi_sp500_status?ids='+encodeURIComponent(ids.slice(0,12).join(',')),{signal:stop.signal});if(!r.ok)throw Error('HTTP '+r.status);const d=await r.json();if(d.error||d.version!==1)throw Error(d.error||'Unsupported diagnostics response');if(!stale(id)){consumeDiagnostics(d);diagnosticError='';}}
  catch(e){if(!stale(id))diagnosticError='Server diagnostics unavailable: '+e.message+'. Check that serve.py was restarted after updating.';}
  finally{clearTimeout(timer);owner.signal.removeEventListener('abort',abort);if(id===run){polling=false;updateDebug();}}
 }
 function pulse(id){if(stale(id)||!busy)return;updateDebug();if(Date.now()-lastPoll>=2000)pollDiagnostics(id);heartbeat=setTimeout(()=>pulse(id),1000);}
 let renderTimer=null;
 function scheduleRender(){if(!renderTimer)renderTimer=setTimeout(()=>{renderTimer=null;render();},150);}
 function render(){
  if(!node?.isConnected)return;
  const s=stats(),visible=[...rows.values()].filter(r=>r.status==='QUALIFIED'&&(filter!=='opportunities'||r.undervalued));
  const detailsOpen=new Set([...node.querySelectorAll('details[open]')].map(d=>d.dataset.evidence));
  node.innerHTML='<section class="vmi-card vmi-scan-header"><p class="vmi-eyebrow">BUSINESS DISCOVERY / S&P 500</p><h3>Great businesses. Price comes next.</h3><p>Every constituent, including share classes. Bulk profitability → projected growth → financial history, efficiency and debt → comparable businesses. Moat is excluded. IBKR snapshots when connected; no orders or GUNS chart requests.</p><div class="vmi-toolbar"><button id="vmi-scan-start" data-scan="start"'+(busy?' disabled':'')+'>Scan S&P 500</button><button data-scan="cancel"'+(!busy?' disabled':'')+'>Cancel scan</button><button data-scan="export"'+(!rows.size?' disabled':'')+'>Export scan evidence</button></div><p id="vmi-scan-progress" role="status" aria-live="polite">'+esc(phase+' · '+progress)+'</p>'+(s.total?'<progress max="'+Math.max(1,stageWork?.total??s.total)+'" value="'+(stageWork?.done||0)+'" aria-label="Current stage listings visited"></progress><p>Current stage '+(stageWork?.done||0)+' / '+(stageWork?.total??s.total)+' visited. Business decisions '+s.assessed+' / '+s.total+' · '+esc(preset)+' preset'+(startedAt?' · started '+esc(new Date(startedAt).toLocaleString()):'')+'</p>':'')+(error?'<p class="vmi-scan-error">'+esc(error)+'</p>':'')+(preset&&preset!==adapter.mode()?'<p>These results use '+esc(preset)+'. The active preset is '+esc(adapter.mode())+'; start a new scan to apply it.</p>':'')+'</section><section id="vmi-scan-debug" class="vmi-card" aria-label="Live scanner diagnostics">'+debugHTML()+'</section><section class="vmi-summary">'+[['qualified','Business-qualified',s.qualified],['opportunities','Qualified + below proposed IV',s.opportunities]].map(([id,label,count])=>'<button data-scan-filter="'+id+'" aria-pressed="'+(filter===id)+'"><strong>'+count+'</strong>'+label+'</button>').join('')+'</section><section class="vmi-card"><h3>Screening results</h3><p>Business qualification does not require undervaluation or an entry signal. Only passing businesses appear here; rejection and unresolved evidence remain in the export. Valuation proposals are not trade prices. Open an evidence trail for formulas, fiscal dates and sources.</p><div class="vmi-scroll"><table><thead><tr><th>Business</th><th>Industry</th><th>Stage / result</th><th>Reason / evidence</th><th>Research</th></tr></thead><tbody>'+visible.map(r=>'<tr><td><strong>'+esc(r.symbol)+'</strong><br><small>'+esc(r.name)+'</small></td><td>'+esc(r.industry)+'</td><td>'+esc(r.status)+'<br><small>'+esc(r.stage||'Queued')+'</small></td><td>'+esc(r.reason||'Queued for acquisition')+(r.result?'<details data-evidence="'+esc(r.symbol)+'"'+(detailsOpen.has(r.symbol)?' open':'')+'><summary>Financial checks and sources</summary><ul>'+r.result.checks.map(c=>'<li><strong>'+esc(c.status+' · '+c.label)+'</strong><p>'+esc(c.detail)+'</p></li>').join('')+'</ul><pre>'+(detailsOpen.has(r.symbol)?esc(JSON.stringify({result:r.result,evidence:r.evidence},null,2)):'')+'</pre></details>':'')+'</td><td><button data-scan-open="'+esc(r.symbol)+'">Open '+esc(r.symbol)+'</button>'+(r.iv?'<p>Proposed IV '+esc(r.iv.toFixed(2))+'<br>'+esc(r.priceSource||'Source price')+' '+esc(r.price)+'</p>':'')+'</td></tr>').join('')+'</tbody></table></div>'+(!visible.length?'<p>No results in this group'+(busy?' yet. The scan is running.':'.')+'</p>':'')+'</section>'+(universe?'<details class="vmi-card"><summary>Universe provenance and numeric interpretation</summary><pre>'+esc(JSON.stringify({source:universe.source,retrievedAt:universe.retrievedAt,basis:universe.basis,policies:[...rows.values()].find(r=>r.result)?.result.policies},null,2))+'</pre></details>':'');
 }
 async function workers(items,limit,id,work){
  let cursor=0;
  await Promise.all(Array.from({length:Math.min(limit,items.length)},async()=>{
   while(!stale(id)){const i=cursor++;if(i>=items.length)return;await work(items[i]);}
  }));
 }
 async function batches(view,candidates,id){
  const chunks=[];for(let i=0;i<candidates.length;i+=20)chunks.push(candidates.slice(i,i+20));
  let completed=0;
  await workers(chunks,3,id,async slice=>{
   try{
    const data=await request('/data/vmi_sp500_batch?view='+view+'&mode='+preset+'&recovery=deferred&symbols='+encodeURIComponent(slice.map(r=>r.symbol).join(',')),id,'Bulk '+view+' '+slice[0].symbol+'–'+slice.at(-1).symbol);
    const allowed=new Set(slice.map(r=>r.symbol));
    if(!Array.isArray(data.rows)||!Array.isArray(data.missing)||data.rows.some(r=>!allowed.has(r.symbol))||data.missing.some(s=>!allowed.has(s)))throw Error('Batch identity mismatch');
    for(const item of data.rows){const r=rows.get(item.symbol);Object.assign(r.fields,item.fields);r.sources.push({url:item.source,retrievedAt:item.retrievedAt});r.stage=view==='financial'?'Bulk profitability':'Projected growth';if(item.screen.rejected){if(r.status!=='REJECTED')stageWork.excluded++;r.status='REJECTED';r.reason=item.screen.reasons.join('; ');}else{r.status='CANDIDATE';r.reason='Passed available '+view+' checks; deeper criteria next';}}
    for(const r of slice)r.visited??={};for(const item of data.rows)rows.get(item.symbol).visited[view]=true;
    for(const symbol of data.missing){const r=rows.get(symbol);r.needsRecovery=true;r.stage='Recovery queued';r.reason='Missing bulk data; no pass inferred';}stageWork.missing+=data.missing.length;
    for(const a of data.attempts||[])note('Bulk '+view+' source issue: '+(a.source||'provider')+' / '+a.error);
   }catch(e){if(stale(id))return;for(const r of slice){r.needsRecovery=true;r.stage='Recovery queued';r.reason=e.message;}stageWork.missing+=slice.length;}
   if(stale(id))return;completed+=slice.length;stageWork.done=completed;progress=view+' data '+completed+' / '+candidates.length;scheduleRender();
  });
 }
 async function recover(candidates,id){
  await workers(candidates,4,id,async r=>{
   try{
    const data=await request('/data/vmi_sp500_recover?symbol='+encodeURIComponent(r.symbol)+'&mode='+preset,id,'Recover '+r.symbol);
    if(!Array.isArray(data.rows)||data.rows.some(x=>x.symbol!==r.symbol))throw Error('Recovery listing mismatch');
    for(const item of data.rows){Object.assign(r.fields,item.fields);r.sources.push({url:item.source,retrievedAt:item.retrievedAt});if(item.screen.rejected){r.status='REJECTED';r.reason=item.screen.reasons.join('; ');stageWork.excluded++;}else{r.status='CANDIDATE';r.reason='Recovered broad fields; full business gates still required';}}
    if(!data.rows.length){stageWork.missing++;r.reason='Public-source recovery unresolved; statement acquisition next';}
    for(const a of data.attempts||[])note(r.symbol+' recovery: '+a.source+' / '+a.error);
   }catch(e){if(stale(id))return;stageWork.missing++;r.reason='Recovery unfinished: '+e.message;}
   if(stale(id))return;stageWork.done++;progress=stageWork.done+' / '+stageWork.total+' individual recoveries';scheduleRender();
  });
 }
 async function deep(candidates,id){
  let completed=0;
  await workers(candidates,4,id,async r=>{
   r.status='ACQUIRING';r.stage='Statements / calculations';scheduleRender();
   try{
    const data=await request('/data/vmi_sp500_company?symbol='+encodeURIComponent(r.symbol)+'&mode='+preset,id,'Detailed '+r.symbol);
    if(data.member.symbol!==r.symbol||data.result.preset!==preset)throw Error('Scan result identity/preset mismatch');
    if(!['QUALIFIED','REJECTED','ACQUIRING'].includes(data.result.status)||data.result.status==='QUALIFIED'&&(data.result.failures.length||data.result.pending.length))throw Error('Inconsistent qualification evidence');
    Object.assign(r,{result:data.result,evidence:data.evidence,research:data.research,status:data.result.status,stage:'Business criteria',reason:data.result.failures.length?data.result.failures.join('; '):data.result.pending.length?'Further acquisition: '+data.result.pending.join(', '):'All applicable scanner gates passed; moat excluded'});
    if(r.research&&root.VMI){try{r.candidate=root.VMI.automatic(root.VMI.company(r.symbol,'USD'),r.research);r.iv=r.candidate.valuations[preset]?.iv;r.price=Number(String(r.research.market?.Price||'').replace(/,/g,''));r.priceSource=r.research.market?.['Price source']||'Public provider reference';r.undervalued=Number.isFinite(r.price)&&r.price>0&&r.iv>r.price;}catch(e){r.valuationNote=e.message;}}
   }catch(e){if(stale(id))return;r.status='ACQUIRING';r.reason='Acquisition did not finish: '+e.message;r.stage='Retrieval repair required';}
   if(stale(id))return;completed++;stageWork.done=completed;if(r.status==='REJECTED')stageWork.excluded++;if(r.status==='ACQUIRING')stageWork.missing++;progress=completed+' / '+candidates.length+' detailed reviews';scheduleRender();
  });
 }
 async function start(){
  if(busy)return;controller?.abort();controller=new AbortController();const id=++run;
  clearTimeout(heartbeat);sequence=0;runToken='scan-'+Date.now().toString(36)+'-'+Math.random().toString(36).slice(2,10);active=new Map();traces=new Map();counts=new Map();totals={};stages=[];log=[];lastDone=null;diagnosticError='';cooldowns={};polling=false;lastPoll=0;stageWork=null;
  busy=true;error='';rows=new Map();preset=adapter.mode();startedAt=Date.now();finishedAt=null;phase='Loading complete constituent universe';progress='';filter='qualified';render();
  try{
   if(typeof document!=='undefined')heartbeat=setTimeout(()=>pulse(id),1000);
   universe=await request('/data/vmi_sp500_members',id,'Load complete constituent universe');
   if(!Array.isArray(universe.rows)||universe.rows.length<490||universe.rows.length>520||new Set(universe.rows.map(r=>r.symbol)).size!==universe.rows.length)throw Error('Incomplete or duplicate universe; refusing a partial scan');
   beginStage('Stage 0 / immediate industry exclusions',universe.rows.length);
   for(const member of universe.rows){const excluded=member.industryExcluded===true;rows.set(member.symbol,{...member,status:excluded?'REJECTED':'QUEUED',fields:{},sources:[],reason:excluded?'Course industry exclusion from verified GICS classification':'Not yet assessed'});if(excluded)stageWork.excluded++;stageWork.done++;}
   endStage();
   const survivors=()=>[...rows.values()].filter(r=>r.status!=='REJECTED');
   let candidates=survivors();beginStage('Stage 1 / fast bulk profitability',candidates.length);await batches('financial',candidates,id);if(stale(id))return;endStage();
   candidates=survivors();beginStage('Stage 2 / fast bulk growth',candidates.length);await batches('growth',candidates,id);if(stale(id))return;endStage();
   candidates=survivors().filter(r=>r.needsRecovery);beginStage('Stage 3 / individual broad-data recovery',candidates.length);await recover(candidates,id);if(stale(id))return;endStage();
   candidates=survivors();beginStage('Stage 4 / statements, efficiency, debt and peers',candidates.length);await deep(candidates,id);if(stale(id))return;endStage();
   finishedAt=Date.now();const s=stats();phase=s.acquiring?'Scan requires acquisition repair':'Scan complete';progress=s.assessed+' assessed; '+s.qualified+' business-qualified; '+s.acquiring+' unresolved';
  }catch(e){if(!stale(id)){error=e.message;phase='Scan stopped';}}
  finally{if(id===run){busy=false;clearTimeout(heartbeat);render();}}
 }
 function cancel(reason='Cancelled — retained results are partial'){run++;controller?.abort();clearTimeout(heartbeat);active.clear();polling=false;busy=false;finishedAt=Date.now();note(reason);phase=reason;progress='';render();}
 function exportScan(){const content={kind:'paper-desk-vmi-sp500',version:1,preset,startedAt,finishedAt,phase,universe,diagnostics:{stages,log,totals,requests:[...traces.values()]},rows:[...rows.values()]};const url=URL.createObjectURL(new Blob([JSON.stringify(encodeExport(content))],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='vmi_sp500_'+new Date().toISOString().slice(0,10)+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),30000);}
 function mount(){const next=document.getElementById('vmi-scanner');if(!next||next===node)return;node=next;node.addEventListener('toggle',e=>{const d=e.target;if(d.open&&d.dataset.evidence){const r=rows.get(d.dataset.evidence),pre=d.querySelector('pre');if(r&&pre&&!pre.textContent)pre.textContent=JSON.stringify({result:r.result,evidence:r.evidence},null,2);}},true);node.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;if(b.dataset.scan){e.stopPropagation();if(b.dataset.scan==='start')start();if(b.dataset.scan==='cancel')cancel();if(b.dataset.scan==='export')exportScan();}if(['qualified','opportunities'].includes(b.dataset.scanFilter)){e.stopPropagation();filter=b.dataset.scanFilter;render();}if(b.dataset.scanOpen){e.stopPropagation();const r=rows.get(b.dataset.scanOpen);if(r?.status==='QUALIFIED')adapter.open(r);}});render();}
 return {view:()=>'<section id="vmi-scanner" aria-label="S&P 500 business scanner"></section>',mount,start,cancel,state:()=>({busy,preset,phase,rows:[...rows.values()],stats:stats(),diagnostics:{stages,log,totals,active:[...active.values()]}})};
}
root.VMIScanner={create,encodeExport,decodeExport};if(typeof module!=='undefined')module.exports=root.VMIScanner;
})(globalThis);
