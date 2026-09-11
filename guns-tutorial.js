/* Isolated training: no account bridge, network, storage or broker contract IDs. */
(function(r,f){if(typeof module==='object'&&module.exports)module.exports=f(require('./guns.js'),require('./guns-workflow.js'));else r.GunsTutorial=f(r.Guns,r.GunsWorkflow);})(globalThis,function(C,W){'use strict';
 const lessons=[
  {symbol:'ZORA-DEMO',setup:1,entry:10.01,stop:9.81,previous:9,volume:240000,news:'FICTIONAL: Zora Solar raises its earnings outlook. There is no fixed-price acquisition.',chart:[9.4,9.65,9.82,9.98,9.91,9.96,9.99],hint:'S1: judge the premarket-high breakout at $10.00. Entry is one cent above. Check daily resistance.',outcome:'target'},
  {symbol:'NIMB-DEMO',setup:2,entry:15.01,stop:14.76,previous:13.5,volume:165000,news:'FICTIONAL: Nimbus Robotics wins a new commercial contract. Read details, not just the headline.',chart:[14.3,14.65,14.99,14.81,14.86,14.93,14.97],hint:'S2: lower pivot at $15.00. Premarket high $15.50 leaves more than 1R clearance. A good-looking setup can still lose.',outcome:'stop'},
  {symbol:'LUMA-DEMO',setup:3,entry:8.01,stop:7.80,previous:7,volume:380000,news:'FICTIONAL: Luma Biotech reports favorable trial results. Verify company, date and clinical details.',chart:[7.3,7.55,7.8,8.16,8.10,8.04,7.99],hint:'S3: premarket bull flag. Last candle high $8.00, low $7.81. This lesson demonstrates a gap above the limit: no chasing.',outcome:'chase'},
  {symbol:'VELA-DEMO',setup:4,entry:20.01,stop:19.76,previous:18,volume:310000,news:'FICTIONAL: Vela Systems raises guidance after earnings. Confirm the actual release and nearby resistance.',chart:[19.2,19.4,19.7,20.16,20.12,20.06,19.99],hint:'S4: judge the FIRST opening bull flag. Last high $20.00, low $19.77. Partial entry and breakeven protection still incur fees.',outcome:'breakeven'}
 ];
 const guidance=[
  'Select the named fictional stock below. Scanner rank helps decide what to inspect; it does not approve chart quality.',
  'Open the fictional news, then acknowledge your review. In real trading verify source/date/relevance and exclude fixed-price buyouts.',
  'YOU judge the chart and setup. Review the described trigger and resistance. You may reject the trade instead.',
  'Change equity: at 1%, $100,000 gives $1,000; $90,000 gives $900; $100,100 gives $1,001. Actual risk is capped by whole shares, fees and buying power.',
  'Confirm using the matching S button or bound key. This is your chart decision. Software arms a stop-limit, not an immediate market order.',
  'Advance the fictional quote. It must respect the limit, buying power and displayed quantity.',
  'Advance again: stop/target/breakeven management is automatic after the entry fills.',
  'Advance to exit. Breakeven is before costs; a stop price is not a guaranteed maximum loss.',
  'Review the scripted result. Continue to the next stock. These examples are lessons, not performance evidence.'
 ];
 function model(){let lesson=0,stage=0,equity=100000,read=false,result='',position=null;let events=[];
  const stock=()=>lessons[lesson],risk=()=>C.size(equity,1,stock().entry+(stock().entry<20?.03:.05),stock().stop,equity,()=>2);
  function reset(){stage=0;read=false;result='';position=null;events=[];}
  const snapshot=()=>({lesson,stage,equity,read,result,position,events:events.slice(),stock:stock(),risk:risk(),guidance:guidance[stage]});
  function action(type,value){const l=stock();
   if(type==='restart'){lesson=0;equity=100000;reset();return snapshot();}
   if(type==='equity'&&stage===3&&[90000,100000,100100].includes(Number(value)))equity=Number(value);
   if(type==='pick'&&stage===0&&value===l.symbol)stage=1;
   if(type==='read'&&stage===1)read=true;
   if(type==='news'&&stage===1&&read)stage=2;
   if(type==='reject'&&stage===2){result='SKIPPED by your chart decision. Not trading is a valid outcome.';stage=8;}
   if(type==='chart'&&stage===2)stage=3;
   if(type==='risk'&&stage===3)stage=4;
   if(type==='confirm'&&stage===4&&Number(value)===l.setup){events.push('USER confirmed S'+l.setup+'; stop-limit armed');stage=5;}
   if(type==='advance'&&stage===5){if(l.outcome==='chase'){result='CANCELLED: ask above limit. No shares bought; no fabricated fill.';events.push('No-chase cancellation');stage=8;}else{const qty=l.setup===4?Math.min(7,risk().qty):risk().qty;position={qty,entry:l.entry,stop:l.stop,target:C.round(l.entry+2*(l.entry-l.stop),.01,true),initialR:l.entry-l.stop,breakeven:true,sessionEnd:999999999};events.push('FILLED '+qty+' shares; stop/target protected; remainder cancelled');stage=6;}}
   else if(type==='advance'&&stage===6){if(l.outcome==='stop'){result='STOP: '+((l.stop-.02-position.entry)*position.qty-2).toFixed(2)+' USD net. Slippage/fees can exceed planned loss.';events.push('Stop triggered; filled below trigger, with costs');position=null;stage=8;}else{position.stop=C.exit(position,{bid:position.entry+position.initialR+.001},0).stop;events.push('+1R reached; stop moved to entry, before fees');stage=7;}}
   else if(type==='advance'&&stage===7){const p=l.outcome==='target'?position.target:position.stop;result=(l.outcome==='target'?'TARGET':'BREAKEVEN STOP')+': '+((p-position.entry)*position.qty-2).toFixed(2)+' USD net after $2 illustrative commissions.';events.push('Linked protection closed; no residual short');position=null;stage=8;}
   if(type==='next'&&stage===8&&lesson<3){lesson++;reset();}return snapshot();
  }
  return {snapshot,action};
 }
 function create(options={}){let dialog=null,m=model();const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function render(){if(!dialog)return;const s=m.snapshot(),l=s.stock,lo=Math.min(...l.chart)-.1,hi=Math.max(...l.chart)+.1,points=l.chart.map((p,i)=>`${20+i*65},${150-(p-lo)/(hi-lo)*120}`).join(' ');
   dialog.innerHTML=`<header><h2 tabindex="-1">GUNS guided tutorial · ${s.lesson+1}/4</h2><button data-lesson="close">Exit tutorial</button></header><p class="guns-warning">ISOLATED SIMULATION: all four stocks, news and prices are fictional. No Gateway needed. No live/paper-account data, settings or orders are changed.</p><nav>${['Candidate','News','Chart','Risk','Confirm','Trigger','Protect','Exit','Review'].map((x,i)=>`<span ${i===s.stage?'class="current"':''}>${i+1}. ${x}</span>`).join('')}</nav><p class="lesson-guide">${esc(s.guidance)}</p><section class="lesson-candidates">${lessons.map(x=>`<button data-lesson="pick" data-value="${x.symbol}" ${s.stage!==0?'disabled':''}>${x.symbol}<small>FICTIONAL · S${x.setup} · ${x.volume.toLocaleString()} PM shares</small></button>`).join('')}</section><section class="lesson-body"><article><h3>Select / review ${l.symbol}</h3><svg viewBox="0 0 460 170" role="img" aria-label="Fictional illustrative price path"><polyline points="${points}" fill="none" stroke="#68b5ff" stroke-width="3"/></svg><small>Illustrative line; candle levels below are authored, not inferred from the line.</small><p>${esc(l.hint)}</p><p>Entry ${l.entry.toFixed(2)} · Stop ${l.stop.toFixed(2)} · Target ${(l.entry+2*(l.entry-l.stop)).toFixed(2)}</p><button data-lesson="read" ${s.stage!==1?'disabled':''}>Read fictional news</button>${s.read?'<blockquote>'+esc(l.news)+'</blockquote>':''}</article><article><h3>Separate training account</h3><label>Marked equity<select data-lesson-equity ${s.stage!==3?'disabled':''}>${[100000,90000,100100].map(e=>`<option value="${e}" ${s.equity===e?'selected':''}>$${e.toLocaleString()}</option>`).join('')}</select></label><p><strong>1% budget: $${s.risk.budget.toLocaleString()}</strong></p><p>Whole shares ${s.risk.qty} · risk $${s.risk.risk.toFixed(2)} · unused $${s.risk.unused.toFixed(2)}</p>${s.position?'<p>PROTECTED '+s.position.qty+' shares · stop '+s.position.stop.toFixed(2)+'</p>':''}<div class="lesson-actions">${s.stage===1?'<button data-lesson="news" '+(!s.read?'disabled':'')+'>I reviewed the catalyst</button>':''}${s.stage===2?'<button data-lesson="chart">I judge this chart suitable for S'+l.setup+'</button><button data-lesson="reject">Reject chart / skip</button>':''}${s.stage===3?'<button data-lesson="risk">Risk understood — continue</button>':''}${s.stage===4?[1,2,3,4].map(n=>'<button data-lesson="confirm" data-value="'+n+'" '+(n!==l.setup?'disabled':'')+'>Confirm S'+n+' ['+esc((options.bindings?.()||W.defaults)[n-1])+']</button>').join(''):''}${s.stage>=5&&s.stage<=7?'<button data-lesson="advance">Advance simulated price</button>':''}${s.stage===8?'<p role="status">'+esc(s.result)+'</p>'+(s.lesson<3?'<button data-lesson="next">Next fictional stock</button>':'<strong>Tutorial complete. Real chart decisions remain yours.</strong>')+'<button data-lesson="restart">Restart tutorial</button>':''}</div><ol>${s.events.map(x=>'<li>'+esc(x)+'</li>').join('')}</ol></article></section>`;
  }
  function close(){if(dialog){dialog.close();dialog.remove();dialog=null;}}
  function open(){if(dialog)return;m=model();dialog=document.createElement('dialog');dialog.id='guns-tutorial';document.body.appendChild(dialog);dialog.addEventListener('click',e=>{const b=e.target.closest('[data-lesson]');if(!b||b.disabled)return;if(b.dataset.lesson==='close')close();else{m.action(b.dataset.lesson,b.dataset.value);render();}});dialog.addEventListener('change',e=>{if(e.target.hasAttribute('data-lesson-equity')){m.action('equity',e.target.value);render();}});dialog.addEventListener('cancel',e=>{e.preventDefault();close();});render();dialog.showModal();dialog.querySelector('h2').focus();}
  if(typeof document!=='undefined')document.addEventListener('keydown',e=>{if(!dialog||e.target.closest?.('input,textarea,select,[contenteditable="true"]'))return;const index=(options.bindings?.()||W.defaults).indexOf(W.chord(e));if(index>=0){e.preventDefault();e.stopImmediatePropagation();m.action('confirm',index+1);render();}},true);
  return {open,close,isOpen:()=>!!dialog};
 }
 return {lessons,model,create};
});
