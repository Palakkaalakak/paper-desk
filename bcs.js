/* Bull call spread (debit spread) rules and paper exits, from the options course (OPTIONS_MASTER_DOCUMENT.md,
   Part II, "The Bull Call Spread", sections 17-18). Pricing comes from BPSX.fillModel. Never calls broker order APIs. */
(function(root,f){if(typeof module==='object'&&module.exports)module.exports=f(require('./bpsx.js'));else root.BCS=f(root.BPSX);})(globalThis,function(BPSX){'use strict';
const finite=x=>typeof x==='number'&&Number.isFinite(x),positive=x=>finite(x)&&x>0;
// Course rules: stock > $20; ~30 DTE (15-50 acceptable); long call at the money or one strike in the money (~0.5-0.6
// delta); short call about one standard deviation up, at least $5 above the long; total premium < 2% of net liquidation;
// risk:reward at least 1:2; take profit at 80% of max profit; cut the loss at 50% of max loss (hold if >80% lost with >= 2 weeks left).
const limits={riskPct:2,minReward:2,minWidth:5,minPrice:20,minDte:15,maxDte:50,targetDte:30,profitPct:80,lossPct:50,gapLossPct:80,gapHoldDays:14,deltaLow:.45,deltaHigh:.70};
const days=(expiry,now=Date.now())=>{const m=/^(\d{4})(\d{2})(\d{2})$/.exec(String(expiry||''));return m?Math.ceil((Date.UTC(+m[1],+m[2]-1,+m[3],20)-now)/86400000):NaN;};
const fresh=(r,now=Date.now(),inspection=false)=>{const q=r?.quote;return !!q&&q.brokerConid===r.conid&&(q.status==='LIVE'||inspection&&q.status==='FROZEN')&&!q.halted&&!q.error&&finite(q.bid)&&finite(q.ask)&&q.bid>=0&&q.ask>0&&q.ask>=q.bid&&finite(q.at)&&now-q.at>=-1000&&now-q.at<60000;};
function economics(long,short,debit,qty){const width=short.strike-long.strike,maxLossPer=debit*100,maxProfitPer=(width-debit)*100,qn=Number.isSafeInteger(qty)&&qty>0?qty:1;
 return {width,debit,maxLoss:maxLossPer*qn,maxProfit:maxProfitPer*qn,breakeven:long.strike+debit,ratio:debit>0?(width-debit)/debit:NaN,tpValue:debit+limits.profitPct/100*(width-debit),slValue:debit*(1-limits.lossPct/100),perContractLoss:maxLossPer};}
// Net debit of the spread under the paper fill rule (mid plus 40% of the mid-to-natural distance), rounded UP a cent so a
// limit at this debit is never below what the market gives. Null if unquoted.
function pricing(long,short){const F=BPSX.fillModel,cq=F.comboQuote([{conid:long.conid,side:'BUY'},{conid:short.conid,side:'SELL'}],{[long.conid]:long.quote,[short.conid]:short.quote},long.spot||short.spot);
 if(!cq)return null;const debit=Math.ceil(F.achievable(cq)*100-1e-9)/100;return {cq,debit,mid:cq.mid,natural:cq.natural,half:cq.half};}
function size(netLiq,debit){if(!positive(netLiq)||!positive(debit))return {qty:0,budget:0,reason:'Account value or debit unavailable'};const budget=limits.riskPct/100*netLiq,qty=Math.floor(budget/(debit*100)+1e-9);
 return {qty,budget,reason:qty?'':'One spread costs $'+(debit*100).toFixed(2)+', more than the '+limits.riskPct+'% risk budget ($'+budget.toFixed(2)+')'};}
function validate(long,short,debit,qty,spot,under,netLiq,now=Date.now(),inspection=false){const out=[];if(!long||!short)return ['Select both call legs'];
 for(const r of [long,short]){if(r.secType!=='OPT'||r.right!=='C'||r.currency!=='USD'||r.mult!==100||r.underConid!==under||!Number.isSafeInteger(r.conid)||r.conid<=0)out.push('Verified standard USD 100-share call contracts required');if(!fresh(r,now,inspection))out.push('Fresh LIVE two-sided IB quotes required');}
 if(long.conid===short.conid||long.expiry!==short.expiry||long.root!==short.root||!(short.strike>long.strike))out.push('Buy the lower call and sell the higher call, same underlying and expiry');
 if(!(spot>limits.minPrice))out.push('Share price must be above $'+limits.minPrice);
 const dte=days(long.expiry,now);if(!(dte>=limits.minDte&&dte<=limits.maxDte))out.push('Expiry should be about '+limits.targetDte+' days ('+limits.minDte+'–'+limits.maxDte+' acceptable); this one is '+dte);
 if(finite(spot)&&long.strike>spot)out.push('Long call must be at the money or one strike in the money');
 const d=long.quote?.greeks?.delta;if(!finite(d)||d<limits.deltaLow||d>limits.deltaHigh)out.push('Long call delta should be roughly 0.5–0.6 (observed '+(finite(d)?d.toFixed(2):'n/a')+')');
 if(short.strike-long.strike<limits.minWidth-1e-9)out.push('Strikes must be at least $'+limits.minWidth+' apart');
 if(!positive(debit))out.push('A positive net debit is required');else{const e=economics(long,short,debit,qty);if(!(e.ratio>=limits.minReward-1e-9))out.push('Risk:reward must be at least 1:'+limits.minReward+' (this is 1:'+(finite(e.ratio)?e.ratio.toFixed(2):'n/a')+')');
  if(positive(netLiq)&&e.maxLoss>limits.riskPct/100*netLiq+1e-6)out.push('Premium at risk $'+e.maxLoss.toFixed(2)+' exceeds '+limits.riskPct+'% of the account ($'+(limits.riskPct/100*netLiq).toFixed(2)+')');}
 return [...new Set(out)];}
// Pick the course spread: long at/one strike in the money nearest 0.55 delta; short nearest one standard deviation up
// (from the long call's own IV), at least $5 higher, with reward:risk of at least 2.
function recommend(calls,spot,now=Date.now(),inspection=false,netLiq=0){const rows=calls.slice().sort((a,b)=>a.strike-b.strike).filter(r=>fresh(r,now,inspection));
 const itm=rows.filter(r=>r.strike<=spot).slice(-2);if(!itm.length)return null;const results=[];
 for(const long of itm){const iv=long.quote?.greeks?.iv,dte=days(long.expiry,now),sd=spot*(finite(iv)&&iv>0?iv:.3)*Math.sqrt(Math.max(1,dte)/365),target=spot+sd;
  for(const short of rows.filter(r=>r.strike>=long.strike+limits.minWidth-1e-9)){const p=pricing(long,short);if(!p)continue;const issues=validate(long,short,p.debit,1,spot,long.underConid,0,now,inspection).filter(m=>!/exceeds|Premium at risk/.test(m));if(issues.length)continue;
   const e=economics(long,short,p.debit,1);results.push({long,short,debit:p.debit,ratio:e.ratio,width:e.width,target,score:Math.abs(short.strike-target)/spot+Math.abs((long.quote.greeks.delta||0)-.55)*.5});}}
 return results.sort((a,b)=>a.score-b.score)[0]||null;}
// Why nothing qualifies, from the quotes that are there.
function explain(calls,spot,now=Date.now(),inspection=false){const rows=calls.slice().sort((a,b)=>a.strike-b.strike),quoted=rows.filter(r=>fresh(r,now,inspection)),itm=quoted.filter(r=>r.strike<=spot).slice(-2),counts=new Map();
 for(const long of itm)for(const short of quoted.filter(r=>r.strike>long.strike)){const p=pricing(long,short);if(!p)continue;for(const m of validate(long,short,p.debit,1,spot,long.underConid,0,now,inspection))if(!/Premium at risk/.test(m))counts.set(m,(counts.get(m)||0)+1);}
 const top=[...counts].sort((a,b)=>b[1]-a[1]).slice(0,3).map(([m])=>m);return rows.length+' calls, '+quoted.length+' with live quotes'+(itm.length?(top.length?'; blocked by: '+top.join(' · '):''):'; no in-the-money/at-the-money call has a live quote yet');}
// Exit thresholds on the spread's mid value (what closing would return per share).
function exitDecision(entry,value,dte){const e=entry,tp=e.tpValue,sl=e.slValue,lossFrac=e.debit>0?(e.debit-value)/e.debit:0;
 if(value>=tp-1e-9)return 'TP';if(lossFrac>=limits.lossPct/100-1e-9){if(lossFrac>limits.gapLossPct/100&&dte>=limits.gapHoldDays)return null;return 'SL';}
 if(dte<=0)return 'EXPIRY';return null;}
function createExecution(a){const position=cid=>a.state().positions.find(p=>p.conid===cid),note=(e,t)=>{if(e.status===t)return false;e.status=t;return true;};
 function owned(entry){const legs=entry.legs||[],l=legs.find(x=>x.side==='BUY'),s=legs.find(x=>x.side==='SELL');if(legs.length!==2||!l||!s||l.right!=='C'||s.right!=='C'||l.expiry!==s.expiry||s.strike<=l.strike)return null;const lp=position(l.conid),sp=position(s.conid),lq=lp?.qty||0,sq=-(sp?.qty||0);if(lq===0&&sq===0)return {closed:true};if(!Number.isSafeInteger(lq)||lq<=0||lq!==sq||lq>entry.filledQty)return null;return {l,s,qty:lq};}
 function live(pair,now){return a.connected()&&[pair.l,pair.s].every(r=>{const q=a.quotes()[r.conid];return a.ready(r.conid)&&q?.brokerConid===r.conid&&q.status==='LIVE'&&!q.error&&!q.halted&&finite(q.at)&&now-q.at>=0&&now-q.at<15000&&finite(q.bid)&&q.bid>=0&&positive(q.ask)&&q.ask>=q.bid;});}
 function guard(o){if(!o.bcsExit)return true;if(a.writable&&!a.writable())return false;const S=a.state(),entry=S.orders.find(e=>e.id===o.bcsExit.entryId),pair=entry&&owned(entry),now=a.now();if(!entry?.bcs?.exits?.enabled||!pair||pair.closed||o.qty-o.filledQty!==pair.qty||!a.session(now)||!live(pair,now))return false;return true;}
 function manage(){if(a.writable&&!a.writable())return false;const S=a.state(),now=a.now();let changed=false;
  for(const entry of S.orders.slice()){const e=entry.bcs?.exits;if(!e?.enabled||entry.status!=='filled'||!Number.isSafeInteger(entry.filledQty))continue;
   const pair=owned(entry);if(!pair){changed=note(e,'RECONCILE_LEGS')||changed;continue;}if(pair.closed){changed=note(e,'CLOSED')||changed;continue;}
   const existing=e.exitOrderId&&S.orders.find(o=>o.id===e.exitOrderId);
   if(e.exitOrderId){if(!existing){changed=note(e,'RECONCILE_EXIT')||changed;continue;}if(existing.status==='working'&&guard(existing)){a.fill(existing);changed=true;}changed=note(e,existing.status==='filled'?'CLOSED':existing.status==='working'?'EXIT_WORKING':'EXIT_CANCELLED')||changed;continue;}
   if(S.orders.some(o=>o.status==='working'&&(o.legs||[o]).some(l=>[pair.l.conid,pair.s.conid].includes(l.conid)))){changed=note(e,'OTHER_ORDER_REQUIRES_REVIEW')||changed;continue;}
   if(!a.session(now)){changed=note(e,'WAITING_REGULAR_SESSION')||changed;continue;}if(!live(pair,now)){changed=note(e,'WAITING_FRESH_LIVE_QUOTES')||changed;continue;}
   const ql=a.quotes()[pair.l.conid],qs=a.quotes()[pair.s.conid],value=(ql.bid+ql.ask)/2-(qs.bid+qs.ask)/2,dte=days(pair.l.expiry,now);e.lastValue=value;
   const reason=exitDecision(e,value,dte);if(!reason){changed=note(e,'ARMED')||changed;continue;}
   const legs=[{...pair.l,side:'SELL',ratio:1},{...pair.s,side:'BUY',ratio:1}],id=a.uid();
   const exit={id,ts:now,day:a.today(),symbol:entry.symbol+' '+reason+' close',strategy:'BCS '+reason,conid:pair.l.conid,secType:'OPT',mult:100,exch:'SMART',side:'SELL',qty:pair.qty,type:'COMBO',limit:null,stop:null,tif:'GTC',status:'working',filledQty:0,avgFill:0,commission:0,triggered:true,legs,bcsExit:{entryId:entry.id,reason,triggerValue:value,triggerAt:now}};
   e.exitOrderId=id;e.reason=reason;e.triggerAt=now;e.status='EXIT_WORKING';S.orders.unshift(exit);a.fill(exit);e.status=exit.status==='filled'?'CLOSED':'EXIT_WORKING';changed=true;}
  if(changed)a.save();return changed;}
 return {manage,guard};}
return {limits,days,fresh,economics,pricing,size,validate,recommend,explain,exitDecision,createExecution};});
