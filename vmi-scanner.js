(function(root){'use strict';
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const done=r=>['REJECTED','QUALIFIED'].includes(r.status);
function create(adapter){
 let run=0,controller=null,busy=false,rows=new Map(),phase='Ready',progress='',preset='',startedAt=null,finishedAt=null,universe=null,filter='qualified',node=null,error='';
 const stats=()=>{const values=[...rows.values()];return {total:values.length,assessed:values.filter(done).length,qualified:values.filter(r=>r.status==='QUALIFIED').length,opportunities:values.filter(r=>r.status==='QUALIFIED'&&r.undervalued).length,rejected:values.filter(r=>r.status==='REJECTED').length,acquiring:values.filter(r=>!done(r)).length};};
 function stale(id){return id!==run||controller?.signal.aborted;}
 async function request(path,id){
  if(stale(id))throw new DOMException('Cancelled','AbortError');
  const timeout=new AbortController(),timer=setTimeout(()=>timeout.abort(),150000);
  const owner=controller,abort=()=>timeout.abort();owner.signal.addEventListener('abort',abort,{once:true});
  try{const response=await fetch(path,{signal:timeout.signal});if(!response.ok)throw Error('Data HTTP '+response.status);const data=await response.json();if(data.error)throw Error(data.error);if(stale(id))throw new DOMException('Cancelled','AbortError');return data;}
  finally{clearTimeout(timer);owner.signal.removeEventListener('abort',abort);}
 }
 let renderTimer=null;
 function scheduleRender(){if(!renderTimer)renderTimer=setTimeout(()=>{renderTimer=null;render();},150);}
 function render(){
  if(!node?.isConnected)return;
  const s=stats(),visible=[...rows.values()].filter(r=>r.status==='QUALIFIED'&&(filter!=='opportunities'||r.undervalued));
  const detailsOpen=new Set([...node.querySelectorAll('details[open]')].map(d=>d.dataset.evidence));
  node.innerHTML='<section class="vmi-card vmi-scan-header"><p class="vmi-eyebrow">BUSINESS DISCOVERY / S&P 500</p><h3>Great businesses. Price comes next.</h3><p>Every constituent, including share classes. Bulk profitability → projected growth → financial history, efficiency and debt → comparable businesses. Moat is excluded. IBKR snapshots when connected; no orders or GUNS chart requests.</p><div class="vmi-toolbar"><button id="vmi-scan-start" data-scan="start"'+(busy?' disabled':'')+'>Scan S&P 500</button><button data-scan="cancel"'+(!busy?' disabled':'')+'>Cancel scan</button><button data-scan="export"'+(!rows.size?' disabled':'')+'>Export scan evidence</button></div><p id="vmi-scan-progress" role="status" aria-live="polite">'+esc(phase+' · '+progress)+'</p>'+(s.total?'<progress max="'+s.total+'" value="'+s.assessed+'" aria-label="Constituents with a financial outcome"></progress><p>'+s.assessed+' / '+s.total+' assessed · '+esc(preset)+' preset'+(startedAt?' · started '+esc(new Date(startedAt).toLocaleString()):'')+'</p>':'')+(error?'<p class="vmi-scan-error">'+esc(error)+'</p>':'')+(preset&&preset!==adapter.mode()?'<p>These results use '+esc(preset)+'. The active preset is '+esc(adapter.mode())+'; start a new scan to apply it.</p>':'')+'</section><section class="vmi-summary">'+[['qualified','Business-qualified',s.qualified],['opportunities','Qualified + below proposed IV',s.opportunities]].map(([id,label,count])=>'<button data-scan-filter="'+id+'" aria-pressed="'+(filter===id)+'"><strong>'+count+'</strong>'+label+'</button>').join('')+'</section><section class="vmi-card"><h3>Screening results</h3><p>Business qualification does not require undervaluation or an entry signal. Only passing businesses appear here; rejection and unresolved evidence remain in the export. Valuation proposals are not trade prices. Open an evidence trail for formulas, fiscal dates and sources.</p><div class="vmi-scroll"><table><thead><tr><th>Business</th><th>Industry</th><th>Stage / result</th><th>Reason / evidence</th><th>Research</th></tr></thead><tbody>'+visible.map(r=>'<tr><td><strong>'+esc(r.symbol)+'</strong><br><small>'+esc(r.name)+'</small></td><td>'+esc(r.industry)+'</td><td>'+esc(r.status)+'<br><small>'+esc(r.stage||'Queued')+'</small></td><td>'+esc(r.reason||'Queued for acquisition')+(r.result?'<details data-evidence="'+esc(r.symbol)+'"'+(detailsOpen.has(r.symbol)?' open':'')+'><summary>Financial checks and sources</summary><ul>'+r.result.checks.map(c=>'<li><strong>'+esc(c.status+' · '+c.label)+'</strong><p>'+esc(c.detail)+'</p></li>').join('')+'</ul><pre>'+(detailsOpen.has(r.symbol)?esc(JSON.stringify({result:r.result,evidence:r.evidence},null,2)):'')+'</pre></details>':'')+'</td><td><button data-scan-open="'+esc(r.symbol)+'">Open '+esc(r.symbol)+'</button>'+(r.iv?'<p>Proposed IV '+esc(r.iv.toFixed(2))+'<br>'+esc(r.priceSource||'Source price')+' '+esc(r.price)+'</p>':'')+'</td></tr>').join('')+'</tbody></table></div>'+(!visible.length?'<p>No results in this group'+(busy?' yet. The scan is running.':'.')+'</p>':'')+'</section>'+(universe?'<details class="vmi-card"><summary>Universe provenance and numeric interpretation</summary><pre>'+esc(JSON.stringify({source:universe.source,retrievedAt:universe.retrievedAt,basis:universe.basis,policies:[...rows.values()].find(r=>r.result)?.result.policies},null,2))+'</pre></details>':'');
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
    const data=await request('/data/vmi_sp500_batch?view='+view+'&mode='+preset+'&symbols='+encodeURIComponent(slice.map(r=>r.symbol).join(',')),id);
    const allowed=new Set(slice.map(r=>r.symbol));
    if(!Array.isArray(data.rows)||!Array.isArray(data.missing)||data.rows.some(r=>!allowed.has(r.symbol))||data.missing.some(s=>!allowed.has(s)))throw Error('Batch identity mismatch');
    for(const item of data.rows){const r=rows.get(item.symbol);Object.assign(r.fields,item.fields);r.sources.push({url:item.source,retrievedAt:item.retrievedAt});r.stage=view==='financial'?'Bulk profitability':'Projected growth';if(item.screen.rejected){r.status='REJECTED';r.reason=item.screen.reasons.join('; ');}else{r.status='CANDIDATE';r.reason='Passed available '+view+' checks; deeper criteria next';}}
    for(const symbol of data.missing){const r=rows.get(symbol);r.stage='Detailed acquisition';r.reason='Individual provider recovery queued';}
   }catch(e){if(stale(id))return;for(const r of slice){r.stage='Detailed fallback queued';r.reason=e.message;}}
   if(stale(id))return;completed+=slice.length;progress=view+' data '+completed+' / '+candidates.length;scheduleRender();
  });
 }
 async function deep(candidates,id){
  let completed=0;
  await workers(candidates,4,id,async r=>{
   r.status='ACQUIRING';r.stage='Statements / calculations';scheduleRender();
   try{
    const data=await request('/data/vmi_sp500_company?symbol='+encodeURIComponent(r.symbol)+'&mode='+preset,id);
    if(data.member.symbol!==r.symbol||data.result.preset!==preset)throw Error('Scan result identity/preset mismatch');
    if(!['QUALIFIED','REJECTED','ACQUIRING'].includes(data.result.status)||data.result.status==='QUALIFIED'&&(data.result.failures.length||data.result.pending.length))throw Error('Inconsistent qualification evidence');
    Object.assign(r,{result:data.result,evidence:data.evidence,research:data.research,status:data.result.status,stage:'Business criteria',reason:data.result.failures.length?data.result.failures.join('; '):data.result.pending.length?'Further acquisition: '+data.result.pending.join(', '):'All applicable scanner gates passed; moat excluded'});
    if(r.research&&root.VMI){try{r.candidate=root.VMI.automatic(root.VMI.company(r.symbol,'USD'),r.research,adapter.discount?.());r.iv=r.candidate.valuations[preset]?.iv;r.price=Number(String(r.research.market?.Price||'').replace(/,/g,''));r.priceSource=r.research.market?.['Price source']||'Public provider reference';r.undervalued=Number.isFinite(r.price)&&r.price>0&&r.iv>r.price;}catch(e){r.valuationNote=e.message;}}
   }catch(e){if(stale(id))return;r.status='ACQUIRING';r.reason='Acquisition did not finish: '+e.message;r.stage='Retrieval repair required';}
   if(stale(id))return;completed++;progress=completed+' / '+candidates.length+' detailed reviews';scheduleRender();
  });
 }
 async function start(){
  if(busy)return;controller?.abort();controller=new AbortController();const id=++run;
  busy=true;error='';rows=new Map();preset=adapter.mode();startedAt=Date.now();finishedAt=null;phase='Loading complete constituent universe';progress='';filter='qualified';render();
  try{
   universe=await request('/data/vmi_sp500_members',id);
   if(!Array.isArray(universe.rows)||universe.rows.length<490||universe.rows.length>520||new Set(universe.rows.map(r=>r.symbol)).size!==universe.rows.length)throw Error('Incomplete or duplicate universe; refusing a partial scan');
   for(const member of universe.rows)rows.set(member.symbol,{...member,status:'QUEUED',fields:{},sources:[],reason:'Not yet assessed'});
   phase='Stage 1 / bulk profitability';render();await batches('financial',[...rows.values()],id);if(stale(id))return;
   phase='Stage 2 / projected growth';await batches('growth',[...rows.values()].filter(r=>r.status!=='REJECTED'),id);if(stale(id))return;
   phase='Stage 3 / statements, efficiency, debt and peers';await deep([...rows.values()].filter(r=>r.status!=='REJECTED'),id);if(stale(id))return;
   finishedAt=Date.now();const s=stats();phase=s.acquiring?'Scan requires acquisition repair':'Scan complete';progress=s.assessed+' assessed; '+s.qualified+' business-qualified; '+s.acquiring+' unresolved';
  }catch(e){if(!stale(id)){error=e.message;phase='Scan stopped';}}
  finally{if(id===run){busy=false;render();}}
 }
 function cancel(reason='Cancelled — retained results are partial'){run++;controller?.abort();busy=false;phase=reason;progress='';render();}
 function exportScan(){const content={kind:'paper-desk-vmi-sp500',version:1,preset,startedAt,finishedAt,phase,universe,rows:[...rows.values()]};const url=URL.createObjectURL(new Blob([JSON.stringify(content)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='vmi_sp500_'+new Date().toISOString().slice(0,10)+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),30000);}
 function mount(){const next=document.getElementById('vmi-scanner');if(!next||next===node)return;node=next;node.addEventListener('toggle',e=>{const d=e.target;if(d.open&&d.dataset.evidence){const r=rows.get(d.dataset.evidence),pre=d.querySelector('pre');if(r&&pre&&!pre.textContent)pre.textContent=JSON.stringify({result:r.result,evidence:r.evidence},null,2);}},true);node.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;if(b.dataset.scan){e.stopPropagation();if(b.dataset.scan==='start')start();if(b.dataset.scan==='cancel')cancel();if(b.dataset.scan==='export')exportScan();}if(['qualified','opportunities'].includes(b.dataset.scanFilter)){e.stopPropagation();filter=b.dataset.scanFilter;render();}if(b.dataset.scanOpen){e.stopPropagation();const r=rows.get(b.dataset.scanOpen);if(r?.status==='QUALIFIED')adapter.open(r);}});render();}
 return {view:()=>'<section id="vmi-scanner" aria-label="S&P 500 business scanner"></section>',mount,start,cancel,state:()=>({busy,preset,phase,rows:[...rows.values()],stats:stats()})};
}
root.VMIScanner={create};if(typeof module!=='undefined')module.exports=root.VMIScanner;
})(globalThis);
