/* BPSX course rules; numerical support proximity is labelled an app heuristic. */
(function(root,f){if(typeof module==='object'&&module.exports)module.exports=f();else root.BPSX=f();})(globalThis,function(){'use strict';
const finite=Number.isFinite,positive=x=>finite(x)&&x>0;
function sma(xs,n){return xs.map((_,i)=>i<n-1?null:xs.slice(i-n+1,i+1).reduce((a,b)=>a+b,0)/n);}
function ema(xs,n){let v=null;return xs.map((x,i)=>{if(i<n-1)return null;v=v===null?xs.slice(0,n).reduce((a,b)=>a+b,0)/n:x*2/(n+1)+v*(1-2/(n+1));return v;});}
function metrics(d,window=252){const bars=d?.bars||[],xs=bars.map(b=>b.c),last=bars.at(-1),iv=(d?.ivHistory||[]).slice(-window),ma={ema20:ema(xs,20),ema40:ema(xs,40),sma50:sma(xs,50),sma150:sma(xs,150)},v=Object.fromEntries(Object.entries(ma).map(([k,x])=>[k,x.at(-1)]));return {ma,...v,price:last?.c,asof:last?.date,
 averageVolume:bars.length>=30&&bars.slice(-30).every(b=>finite(b.v))?bars.slice(-30).reduce((a,b)=>a+b.v,0)/30:null,
 ivPercentile:iv.length>=window&&positive(iv.at(-1)?.value)?100*iv.filter(b=>b.value<=iv.at(-1).value).length/iv.length:null,
 uptrend:positive(v.ema20)&&positive(v.ema40)&&positive(v.sma150)?v.ema20>v.ema40&&v.sma50>v.sma150:null,
 nearSupport:positive(v.sma50)&&positive(last?.c)?Math.abs(last.c/v.sma50-1)<=.03:null,
 pullback:xs.length>=10?xs.at(-1)<Math.max(...xs.slice(-10)):null,
 monthlyDrop:xs.length>=172?xs.slice(22).some((x,i)=>x/xs[i]-1<=-.15):null};}
function screen(d,cfg={}){const m=metrics(d,cfg.ivWindow||252),reasons=[],pending=[],age=Date.now()-Date.parse(m.asof+'T00:00:00Z');
 if(!finite(age)||age<0||age>6*86400000)pending.push('Recent completed daily history');
 if(d?.ivHistory?.at(-1)?.date!==m.asof)pending.push('IV history aligned with daily chart');
 for(const [value,pass,label] of [[m.price,m.price>=50,'Price ≥$50'],[m.averageVolume,m.averageVolume>1000000,'30-session average volume >1m'],[m.ivPercentile,m.ivPercentile>=35,'IV percentile ≥35']]){if(!finite(value))pending.push(label);else if(!pass)reasons.push(label);}
 if(cfg.trend){if(m.uptrend===null)pending.push('150-session trend history');else if(!m.uptrend)reasons.push('20 EMA >40 EMA AND 50 SMA >150 SMA');}
 if(cfg.support&&!(m.nearSupport&&m.pullback))reasons.push('Pullback within 3% of 50 SMA (app heuristic)');
 if(cfg.drawdown){if(m.monthlyDrop===null)pending.push('Monthly decline history');else if(m.monthlyDrop)reasons.push('Observed ≥15% decline across 22 sessions');}
 if(/biotech|pharma/i.test((d?.industry||'')+' '+(d?.category||'')))reasons.push('Biotech/pharma excluded');
 return {m,reasons,pending,status:reasons.length?'FAIL':pending.length?'UNVERIFIED':'PASS'};}
function days(expiry,now=Date.now()){if(!/^\d{8}$/.test(expiry||''))return NaN;return Math.round((Date.parse(expiry.slice(0,4)+'-'+expiry.slice(4,6)+'-'+expiry.slice(6,8)+'T00:00:00Z')-Date.parse(new Date(now).toISOString().slice(0,10)+'T00:00:00Z'))/86400000);}
function expiration(xs,now=Date.now()){return xs.filter(x=>days(x,now)>=20&&days(x,now)<=45).sort((a,b)=>Math.abs(days(a,now)-26)-Math.abs(days(b,now)-26))[0]||null;}
function fresh(r,now=Date.now(),inspection=false){const q=r?.quote;return !!q&&!!r&&q.brokerConid===r.conid&&(q.status==='LIVE'||inspection&&q.status==='FROZEN')&&!q.halted&&!q.error&&finite(q.bid)&&q.bid>=0&&positive(q.ask)&&q.ask>=q.bid&&finite(q.at)&&now-q.at>=-1000&&now-q.at<60000;}
// Feed type is independent of liquidity. Never rewrite raw bid/ask prices.
// Spread rule, derived from the strategy's own payoff arithmetic (no flat $ or %-of-price figure:
// quoted option widths are set by tick size and barely move with stock price or strike).
// Per share, with credit C and short-put delta d taken as the loss probability (win rate 1-d):
//   take-profit closes at 90% of C  -> win  = +0.9C
//   stop closes at a 4C debit       -> loss = -3C
//   gross expected value            EV = 0.9C(1-d) - 3C*d       (break-even win rate is 3/3.9 = 76.9%)
// Entering at the natural price concedes (short width + long width)/2 versus mid; the stop exit
// (probability ~d) crosses a similar market again, the take-profit exit is near-worthless and ~free:
//   round-trip friction  F ~ concession * (1 + d)
// Friction is certain while the edge is only estimated, so by default it may use at most a quarter of EV:
//   allowed concession / midCredit = edgeShare * (0.9(1-d) - 3d) / (1 + d)
// -> about 12.6% at 9 delta, 11.6% at 10, 9.6% at 12, 6.8% at 15, and none once EV is not positive.
const limits={edgeShare:25,ignore:false};
const allowedFriction=d=>{d=Math.abs(d);if(!finite(d)||d<=0||d>=1)return 0;return Math.max(0,limits.edgeShare/100*(.9*(1-d)-3*d)/(1+d));};
const legMid=r=>finite(r?.quote?.bid)&&finite(r?.quote?.ask)?(r.quote.bid+r.quote.ask)/2:null;
function spreadFriction(s,l){const ms=legMid(s),ml=legMid(l);if(ms===null||ml===null)return null;const concession=((s.quote.ask-s.quote.bid)+(l.quote.ask-l.quote.bid))/2,midCredit=ms-ml;return {concession,midCredit,ratio:midCredit>0?concession/midCredit:Infinity};}
function quoteQuality(r,now=Date.now(),inspection=false){const q=r?.quote,width=finite(q?.bid)&&finite(q?.ask)?q.ask-q.bid:null,mid=legMid(r);let label=!q?'No current market':q.error||r.quoteError?'Quote error':!finite(q.bid)||!positive(q.ask)?'Missing market':width<0?'Crossed market':width>mid?'WIDE — spread exceeds the option\u2019s own mid; not a real market':!fresh(r,now,inspection)?'Not fresh LIVE market':'Width OK — not execution assurance';return {width,label,usable:!r?.quoteError&&fresh(r,now,inspection)&&finite(width)&&width>=0&&width<=mid,max:null};}
// Course premium target with conservative floor rounding (app policy).
function size({allocation,credit,width,usage,available,feePerSpread=0,accountQty=Number.MAX_SAFE_INTEGER}){
 const target=allocation/100,perCredit=credit*100,perRisk=(width-credit)*100;
 const fail=reason=>({qty:0,maxQty:0,target,perCredit,perRisk,reason});
 if(!positive(allocation)||!positive(credit)||!positive(perRisk)||!usage||!finite(available)||!finite(feePerSpread)||feePerSpread<0||!Number.isSafeInteger(accountQty)||accountQty<0)return fail('Sizing inputs/account limits unavailable');
 if(usage.error)return fail(usage.error);
 if(![usage.trades,usage.premium,usage.risk,usage.pendingRisk].every(x=>finite(x)&&x>=0))return fail('Account exposure unverified');
 if(usage.trades>=10)return fail('Ten open/pending BPSX trades already');
 if(perRisk/perCredit>10.00000001)return fail('Maximum loss exceeds 10× credit');
 const floor=x=>Math.max(0,Math.floor(x+1e-9)),targetQty=floor(target/perCredit),premiumQty=floor((allocation/10-usage.premium)/perCredit),riskQty=floor((allocation-usage.risk)/perRisk),fundsQty=floor((available-usage.pendingRisk)/(perRisk+feePerSpread)),maxQty=Math.min(premiumQty,riskQty,fundsQty,accountQty),qty=Math.min(targetQty,maxQty);
 return {qty,maxQty,target,perCredit,perRisk,targetQty,premiumQty,riskQty,fundsQty,reason:!qty?(targetQty===0?'One spread exceeds the per-trade premium target; no forced minimum':'No capacity within premium / risk / available-funds limits'):qty<targetQty?'Reduced by remaining account / strategy capacity':'Rounded down to whole spreads at the course premium target'};
}
function exposure(state){
 const positions=state?.positions||[],orders=state?.orders||[];let trades=0,premium=0,risk=0,pendingRisk=0,error='';
 for(const o of orders){if(!o.bpsx||!['working','filled'].includes(o.status))continue;const legs=o.legs||[],held=legs.map(l=>Math.abs(positions.find(p=>p.conid===l.conid)?.qty||0));if(o.status==='filled'&&!held.some(Boolean))continue;
  const n=o.status==='working'?o.qty:Math.max(...held),credit=o.status==='filled'&&finite(o.avgFill)&&o.avgFill<0?-o.avgFill:o.bpsx.credit,loss=(o.bpsx.width-credit)*100*n;
  if(legs.length!==2||!Number.isSafeInteger(n)||n<=0||!positive(credit)||!positive(loss)||o.status==='filled'&&held[0]!==held[1]){error='Reconcile BPSX exposure before sizing';continue;}
  trades++;premium+=credit*100*n;risk+=loss;if(o.status==='working')pendingRisk+=loss;
 }
 return {trades,premium,risk,pendingRisk,error};
}
function economics(s,l,credit,qty=1){const width=s?.strike-l?.strike;return {credit:credit*100*qty,maxProfit:credit*100*qty,maxLoss:(width-credit)*100*qty,ratio:(width-credit)/credit,breakeven:s?.strike-credit,width,takeProfitDebit:credit*.1,lossAlertDebit:credit*4,takeProfitProfit:credit*90*qty,stopLossLoss:credit*300*qty,takeProfitCost:credit*10*qty,stopLossCost:credit*400*qty};}
function validate(s,l,credit,qty,spot,under,now=Date.now(),inspection=false){const out=[];if(!s||!l)return ['Select both put legs'];
 for(const r of [s,l]){if(r.secType!=='OPT'||r.right!=='P'||r.currency!=='USD'||r.mult!==100||r.underConid!==under||!Number.isSafeInteger(r.conid)||r.conid<=0||r.deliverableStatus!=='IB_STANDARD_CLASS_NOT_OCC_VERIFIED')out.push('Verified standard USD 100-share put contracts required');if(!fresh(r,now,inspection))out.push('Fresh LIVE two-sided IB quotes required');}
 {const f=spreadFriction(s,l),allow=allowedFriction(s.quote?.greeks?.delta);if(f&&!limits.ignore&&!(f.ratio<=allow*1.0000001))out.push('Entry spread cost $'+f.concession.toFixed(2)+' is '+(Number.isFinite(f.ratio)?(f.ratio*100).toFixed(1)+'% of the mid credit':'more than the whole credit')+'; at this delta the trade can afford '+(allow*100).toFixed(1)+'% ('+limits.edgeShare+'% of its expected edge)');}
 if(s.conid===l.conid||s.root!==l.root||s.expiry!==l.expiry||!positive(l.strike)||s.strike<=l.strike||!positive(spot)||s.strike>=spot)out.push('Sell higher OTM put / buy lower put, same underlying and expiry');
 if(!(days(s.expiry,now)>=20&&days(s.expiry,now)<=45))out.push('Select 20–45 DTE');
 const delta=s.quote?.greeks?.delta;if(!finite(delta)||delta>-.09||delta<-.15)out.push('Observed short put delta must be −0.09 to −0.15');
 if(!positive(credit)||!Number.isSafeInteger(qty)||qty<=0)out.push('Positive credit / whole spread quantity required');
 const e=economics(s,l,credit,qty);if(!positive(e.maxLoss)||!finite(e.ratio)||e.ratio>10.00000001)out.push('Maximum loss must be positive and ≤10× credit');return [...new Set(out)];}
function recommend(rows,spot,now=Date.now(),inspection=false){rows=rows.slice().sort((a,b)=>a.strike-b.strike);const results=[];for(let i=0;i<rows.length;i++){const s=rows[i];for(const step of [1,2]){const l=rows[i-step];if(!l)continue;const credit=Math.round(((s.quote?.bid||0)-(l.quote?.ask||0))*10000)/10000;if(validate(s,l,credit,1,spot,s.underConid,now,inspection).length)continue;results.push({short:s,long:l,credit,step,buffer:1-s.strike/spot,score:Math.abs(s.quote.greeks.delta+.1)+(1-s.strike/spot<.15?.1:0)});}}return results.sort((a,b)=>a.score-b.score||a.step-b.step)[0]||null;}
function explain(rows,spot,now=Date.now(),inspection=false){rows=rows.slice().sort((a,c)=>a.strike-c.strike);const quoted=rows.filter(r=>r.quote&&finite(r.quote.bid)&&finite(r.quote.ask)),band=quoted.filter(r=>finite(r.quote.greeks?.delta)&&r.quote.greeks.delta<=-.09&&r.quote.greeks.delta>=-.15),counts=new Map();for(const s of band){for(const step of [1,2]){const l=rows[rows.indexOf(s)-step];if(!l)continue;const credit=Math.round(((s.quote.bid||0)-(l.quote?.ask||0))*10000)/10000;for(const m of validate(s,l,credit,1,spot,s.underConid,now,inspection))counts.set(m,(counts.get(m)||0)+1);}}
 const top=[...counts].sort((a,c)=>c[1]-a[1]).slice(0,3).map(([m])=>m);return rows.length+' puts, '+quoted.length+' with bid/ask, '+band.length+' with delta −0.09…−0.15'+(band.length?'; blocked by: '+(top.join(' · ')||'unknown'):(quoted.length?'; no strike has a usable delta in the band yet (Greeks may be missing)':'; waiting for quotes'));}
function bollinger(xs,n=20,k=2){if(!Number.isInteger(n)||n<2||n>200||!positive(k)||k>5)throw Error('Bollinger settings: period 2–200; deviations >0 to 5');const middle=sma(xs,n),upper=[],lower=[];for(let i=0;i<xs.length;i++){if(i<n-1){upper.push(null);lower.push(null);continue;}const sd=Math.sqrt(xs.slice(i-n+1,i+1).reduce((v,x)=>v+(x-middle[i])**2,0)/n);upper.push(middle[i]+k*sd);lower.push(middle[i]-k*sd);}return {middle,upper,lower};}
function deltaText(x){if(!finite(x))return '—';return x===0?'0':Number(x.toPrecision(5)).toString();}
// Browser-local paper bracket. No network or broker order API exists here.
function createExecution(a){
 const position=cid=>a.state().positions.find(p=>p.conid===cid),note=(e,text)=>{if(e.status===text)return false;e.status=text;return true;};
 function owned(entry){const legs=entry.legs||[],s=legs.find(l=>l.side==='SELL'),l=legs.find(l=>l.side==='BUY');if(legs.length!==2||!s||!l||s.right!=='P'||l.right!=='P'||s.ratio!==1||l.ratio!==1||s.expiry!==l.expiry||s.underConid!==l.underConid||s.strike<=l.strike)return null;const sp=position(s.conid),lp=position(l.conid),sq=-(sp?.qty||0),lq=lp?.qty||0;if(sq===0&&lq===0)return {closed:true};if(!Number.isSafeInteger(sq)||sq<=0||sq!==lq||sq>entry.filledQty)return null;return {s,l,qty:sq};}
 function live(pair,now){return a.connected()&&[pair.s,pair.l].every(r=>{const q=a.quotes()[r.conid];return a.ready(r.conid)&&q?.brokerConid===r.conid&&q.status==='LIVE'&&!q.error&&!q.halted&&finite(q.at)&&now-q.at>=0&&now-q.at<15000&&finite(q.bid)&&q.bid>=0&&positive(q.ask)&&q.ask>=q.bid;});}
 function guard(o){if(!o.bpsxExit)return true;if(a.writable&&!a.writable())return false;const S=a.state(),entry=S.orders.find(e=>e.id===o.bpsxExit.entryId),pair=entry&&owned(entry),now=a.now();if(!entry?.bpsx?.exits?.enabled||!pair||pair.closed||o.qty-o.filledQty!==pair.qty||!a.session(now)||!live(pair,now))return false;if(S.orders.some(other=>other!==o&&other.status==='working'&&(other.legs||[other]).some(l=>[pair.s.conid,pair.l.conid].includes(l.conid))))return false;const expected=[pair.s,pair.l];return o.legs?.length===2&&o.legs.every((l,i)=>l.conid===expected[i].conid&&l.side===(i===0?'BUY':'SELL')&&l.ratio===1);}
 function manage(){if(a.writable&&!a.writable())return false;const S=a.state(),now=a.now();let changed=false;
  for(const entry of S.orders.slice()){
   const e=entry.bpsx?.exits;if(!e?.enabled||entry.status!=='filled'||!positive(-entry.avgFill)||!Number.isSafeInteger(entry.filledQty))continue;
   const pair=owned(entry);if(!pair){changed=note(e,'RECONCILE_LEGS')||changed;continue;}if(pair.closed){changed=note(e,'CLOSED')||changed;continue;}
   const existing=e.exitOrderId&&S.orders.find(o=>o.id===e.exitOrderId);
   if(e.exitOrderId){if(!existing){changed=note(e,'RECONCILE_EXIT')||changed;continue;}if(existing.status==='working'&&guard(existing)){a.fill(existing);changed=true;}changed=note(e,existing.status==='filled'?'CLOSED':existing.status==='working'?'EXIT_WORKING':existing.status==='cancelled'?'EXIT_CANCELLED_MANUALLY':'EXIT_REJECTED')||changed;continue;}
   if(S.orders.some(o=>o.status==='working'&&(o.legs||[o]).some(l=>[pair.s.conid,pair.l.conid].includes(l.conid)))){changed=note(e,'OTHER_ORDER_REQUIRES_REVIEW')||changed;continue;}
   const session=a.session(now);if(!session){changed=note(e,'WAITING_REGULAR_SESSION')||changed;continue;}if(!live(pair,now)){changed=note(e,'WAITING_FRESH_LIVE_QUOTES')||changed;continue;}
   const credit=-entry.avgFill,qs=a.quotes()[pair.s.conid],ql=a.quotes()[pair.l.conid],debit=qs.ask-ql.bid;
   if(!finite(debit)||debit<0){changed=note(e,'INVALID_SPREAD_MARKET')||changed;continue;}
   e.entryCredit=credit;e.tpDebit=credit*.1;e.slDebit=credit*4;e.lastDebit=debit;
   const today=new Intl.DateTimeFormat('en-CA',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(now)).replaceAll('-','');
   const reason=debit>=e.slDebit-1e-9?'SL':debit<=e.tpDebit+1e-9?'TP':pair.s.expiry===today&&now>=session.end-5*60000?'EXPIRY':null;
   if(!reason){changed=note(e,'ARMED')||changed;continue;}
   const legs=[{...pair.s,side:'BUY',ratio:1},{...pair.l,side:'SELL',ratio:1}],id=a.uid();
   const exit={id,ts:now,day:a.today(),symbol:entry.symbol+' '+reason+' close',strategy:'BPSX '+reason,conid:pair.s.conid,secType:'OPT',mult:100,exch:'SMART',side:'BUY',qty:pair.qty,type:'COMBO',limit:reason==='TP'?e.tpDebit:null,stop:null,tif:'GTC',status:'working',filledQty:0,avgFill:0,commission:0,triggered:true,legs,bpsxExit:{entryId:entry.id,reason,triggerDebit:debit,triggerAt:now,entryCredit:credit}};
   // Persist the latch before the fill: repeated ticks/reloads cannot create duplicates.
   e.exitOrderId=id;e.reason=reason;e.triggerAt=now;e.status='EXIT_WORKING';S.orders.unshift(exit);a.fill(exit);e.status=exit.status==='filled'?'CLOSED':'EXIT_WORKING';changed=true;
  }
  if(changed)a.save();return changed;
 }
 return {manage,guard};
}
return {sma,ema,metrics,screen,days,expiration,fresh,quoteQuality,size,exposure,economics,validate,recommend,explain,limits,allowedFriction,spreadFriction,bollinger,deltaText,createExecution};});
