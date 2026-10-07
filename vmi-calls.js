/* IB covered-call research and explicit ticket preparation; never submits orders. */
(function(root){'use strict';
const need=(ok,msg)=>{if(!ok)throw Error(msg);},positive=n=>Number.isFinite(n)&&n>0;
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function coverage(state,stock,excludeId){
 need(stock?.secType==='STK'&&Number.isSafeInteger(stock.conid)&&positive(stock.qty),'Existing long stock position required');let reserved=0;
 const related=x=>x.underConid===stock.conid||x.root===stock.symbol||x.symbol===stock.symbol||String(x.symbol||'').startsWith(stock.symbol+' ');
 function reserve(x,n){
  if(!related(x)||n<=0)return;
  if(x.secType==='STK'){if(x.conid===stock.conid)reserved+=n;return;}
  if(x.right==='P')return;
  need(x.secType==='OPT'&&x.right==='C'&&x.mult===100&&x.deliverableConfirmed===true,'Existing/pending derivative deliverable is unverified; reconcile before reusing coverage');
  need(Number.isSafeInteger(n),'Invalid option quantity');reserved+=n*100;
 }
 for(const x of state.positions||[])if(x.qty<0)reserve(x,-x.qty);
 for(const o of state.orders||[]){
  if(o.id===excludeId||!['working','pending','submitted'].includes(o.status))continue;
  const remaining=o.qty-(o.filledQty||0);need(Number.isFinite(remaining)&&remaining>=0,'Invalid working order quantity');
  for(const l of o.legs||[o])if(l.side==='SELL')reserve(l,remaining*(l.ratio||1));
 }
 return {owned:stock.qty,reserved,available:Math.max(0,stock.qty-reserved),contracts:Math.max(0,Math.floor((stock.qty-reserved)/100))};
}
function ticket(state,stock,d,qty,limit,confirmed,now=Date.now()){
 const i=d?.inst,q=d?.quote;
 need(d?.served_by==='tws'&&i?.secType==='OPT'&&i.right==='C'&&i.currency==='USD'&&i.mult===100&&Number.isSafeInteger(i.conid)&&i.conid>0&&i.underConid===stock?.conid&&i.root===stock.symbol&&i.tradingClass===stock.symbol&&i.deliverableStatus==='IB_STANDARD_CLASS_NOT_OCC_VERIFIED','IB option/underlying identity mismatch');
 need(/^\d{8}$/.test(i.expiry)&&i.expiry>=new Date(now).toISOString().slice(0,10).replaceAll('-',''),'Expired or invalid option');
 need(confirmed===true,'Confirm the OCC deliverable is 100 ordinary shares without adjustments');
 need(q?.brokerConid===i.conid&&positive(q.bid)&&positive(q.ask)&&q.ask>=q.bid&&!q.halted&&!q.error&&q.status==='LIVE'&&Number.isFinite(q.at)&&now-q.at>=-1000&&now-q.at<60000,'Fresh LIVE two-sided IB quote required for handoff');
 need(Number.isSafeInteger(qty)&&qty>0&&positive(limit),'Positive whole contracts and limit premium required');
 need(qty<=coverage(state,stock).contracts,'Insufficient unreserved shares');
 return {...i,deliverableConfirmed:true,vmiCoveredCall:true};
}
function create(a){
 let el=null,selection=null,seq=0,busy=false,status='',draft={qty:'1',limit:'',fees:'0',confirmed:false};
 const stocks=()=>a.positions().filter(p=>p.secType==='STK'&&p.qty>0);
 const active=()=>a.active()&&el?.isConnected;
 async function request(url){const r=await fetch(url,{signal:AbortSignal.timeout(105000)});if(!r.ok)throw Error('IB HTTP '+r.status);const d=await r.json();if(d.error)throw Error(d.error);return d;}
 const protect=fn=>async(...args)=>{try{await fn(...args);}catch(e){status=e.message;render();}};
 function render(){
  if(!active())return;
  if(selection&&selection.bookId!==a.currentBook().id){selection=null;seq++;}
  const s=selection,held=stocks(),stock=held.find(p=>p.conid===s?.stock.conid);let cover='';
  if(stock)try{const c=a.coverage(stock.conid);cover=c.available+' unreserved shares / '+c.contracts+' calls ('+c.reserved+' shares reserved)';}catch(e){cover=e.message;}
  const q=s?.observation?.quote,i=s?.observation?.inst;
  el.innerHTML='<section class="vmi-card"><h3>IB covered-call chain · '+esc(a.currentBook().name)+'</h3><p>Use stock you already own. IB supplies actual expirations, strikes, contract IDs and quotes. Only the selected contract is quoted, automatically every 30 seconds while this view is visible.</p><form id="vmi-call-chain"><label>Existing stock<select name="stock">'+held.map(p=>'<option value="'+p.conid+'"'+(stock?.conid===p.conid?' selected':'')+'>'+esc(p.symbol+' · '+p.qty+' shares')+'</option>').join('')+'</select></label><button'+(!held.length?' disabled':'')+'>Load IB expirations</button></form>'+(s?'<p>'+esc(cover)+'</p><label>Expiration<select id="vmi-call-expiry"><option value="">Choose expiry</option>'+s.chain.expirations.map(e=>'<option'+(e===s.chain.expiry?' selected':'')+'>'+e+'</option>').join('')+'</select></label>'+(s.chain.expiry?'<label>Listed call strike<select id="vmi-call-contract">'+s.chain.calls.map(c=>'<option value="'+c.conid+'"'+(c.conid===s.selected?' selected':'')+'>'+esc(c.strike+' · '+c.symbol+' · #'+c.conid)+'</option>').join('')+'</select></label><p>'+s.chain.calls.length+' calls; '+s.chain.excluded+' ambiguous/adjusted definitions excluded.</p>':''):'')+'<p id="vmi-call-status" role="status">'+esc(status)+'</p></section>'+(q?'<section class="vmi-card"><h3>'+esc(i.symbol)+'</h3><p>Bid '+esc(q.bid??'Unavailable')+' · Ask '+esc(q.ask??'Unavailable')+' · Last '+esc(q.last??'Unavailable')+' · '+esc(q.status)+' · IB observation '+esc(q.at?new Date(q.at).toISOString():'Unavailable')+'</p><p>Greeks: '+esc(JSON.stringify(q.greeks||{}))+'</p><p>IB metadata matches the underlying, call, USD, 100 multiplier and standard trading class. It does not prove the OCC deliverable. Confirm 100 ordinary shares with no cash/adjustments. Delayed, frozen or stale quotes cannot prepare a ticket.</p><form id="vmi-call-ticket"><div class="vmi-grid"><label>Call contracts<input name="qty" type="number" min="1" step="1" value="'+esc(draft.qty)+'"></label><label>Limit premium per share USD<input name="limit" type="number" min="0.01" step="any" value="'+esc(draft.limit)+'"></label><label>Estimated total fees USD<input name="fees" type="number" min="0" step="any" value="'+esc(draft.fees)+'"></label></div><label><input name="confirmed" type="checkbox"'+(draft.confirmed?' checked':'')+'> I checked the OCC deliverable: 100 ordinary shares, no adjustments</label><button>Preview payoff and open SELL ticket</button></form></section>':'')+'<p>Coverage subtracts held short calls, working sell calls/combo legs and stock sales. Unknown existing deliverables block handoff. Existing cash-account and short-selling restrictions remain unchanged. This does not enable shorting or submit any order.</p>';
 }
 async function load(stockId,expiry){
  const stock=stocks().find(p=>p.conid===stockId);need(stock,'Select an existing stock position');const id=++seq,bookId=a.currentBook().id;
  selection=null;status='Loading IB definitions…';render();
  const chain=await request('/data/vmi_chain?symbol='+encodeURIComponent(stock.symbol)+(expiry?'&expiry='+expiry:''));
  if(id!==seq||bookId!==a.currentBook().id||!active())return;
  need(chain.served_by==='tws'&&chain.underConid===stockId,'IB underlying identity mismatch');
  selection={stock,bookId,chain,selected:null};draft={qty:'1',limit:'',fees:'0',confirmed:false};
  if(chain.calls.length)selection.selected=chain.calls.reduce((x,y)=>Math.abs(x.strike-stock.avgCost)<Math.abs(y.strike-stock.avgCost)?x:y).conid;
  status='Definitions retrieved';render();if(selection.selected)await refresh();
 }
 async function refresh(){
  if(!selection?.selected||busy||!active())return;const s=selection,id=s.selected,generation=seq;busy=true;
  try{const d=await request('/data/vmi_option?symbol='+encodeURIComponent(s.stock.symbol)+'&conid='+id);
   if(s!==selection||generation!==seq||s.bookId!==a.currentBook().id||!active())return;
   need(d.served_by==='tws'&&d.inst?.conid===id&&d.inst?.underConid===s.stock.conid,'IB option identity mismatch');
   s.observation=d;if(!draft.limit&&positive(d.quote?.bid))draft.limit=String(d.quote.bid);status='Checked '+new Date(d.retrievedAt).toLocaleTimeString();
  }catch(e){if(s===selection)status='Quote unavailable: '+e.message;}finally{busy=false;if(!el?.contains(document.activeElement)||!document.activeElement?.matches('input,select'))render();if(selection?.selected&&selection.selected!==id)protect(refresh)();}
 }
 function mount(){const node=document.getElementById('vmi-calls-root');if(!node||node===el)return;el=node;
  el.addEventListener('input',e=>{if(e.target.closest('#vmi-call-ticket'))draft[e.target.name]=e.target.type==='checkbox'?e.target.checked:e.target.value;});
  el.addEventListener('change',e=>{e.stopPropagation();protect(async()=>{if(e.target.id==='vmi-call-expiry')await load(selection.stock.conid,e.target.value);if(e.target.id==='vmi-call-contract'){seq++;selection.selected=Number(e.target.value);selection.observation=null;draft={qty:'1',limit:'',fees:'0',confirmed:false};render();await refresh();}})();});
  el.addEventListener('submit',e=>{e.preventDefault();e.stopPropagation();protect(async()=>{
   if(e.target.id==='vmi-call-chain'){await load(Number(new FormData(e.target).get('stock')));return;}
   const s=selection,id=s?.selected,stock=stocks().find(p=>p.conid===s?.stock.conid);need(s?.observation&&stock,'Select a quoted call');
   const qty=Number(draft.qty),limit=Number(draft.limit),fees=Number(draft.fees);need(Number.isFinite(fees)&&fees>=0,'Nonnegative fees required');
   need(draft.confirmed,'Confirm the ordinary OCC deliverable first');
   const payoff=root.VMI.coveredCall({owned:stock.qty,contracts:qty,deliverable:100,cost:stock.avgCost,strike:s.observation.inst.strike,premium:limit,fees});
   if(!confirm('Hypothetical expiry payoff (no early assignment/dividends):\n'+JSON.stringify(payoff,null,2)+'\nPrepare SELL ticket only?'))return;
   await a.openCall(stock.conid,id,qty,limit,draft.confirmed,()=>selection===s&&s.selected===id&&active());
  })();});render();protect(refresh)();
 }
 setInterval(()=>{if(!document.hidden&&active())protect(refresh)();},30000);
 return {view:()=>'<section id="vmi-calls-root" aria-label="IB covered calls"></section>',mount};
}
root.VMICalls={coverage,ticket,create};if(typeof module!=='undefined')module.exports=root.VMICalls;
})(globalThis);
