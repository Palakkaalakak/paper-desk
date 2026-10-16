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
function fresh(r,now=Date.now()){const q=r?.quote;return q?.brokerConid===r?.conid&&q.status==='LIVE'&&!q.halted&&!q.error&&positive(q.bid)&&positive(q.ask)&&q.ask>=q.bid&&finite(q.at)&&now-q.at>=-1000&&now-q.at<60000;}
function economics(s,l,credit,qty=1){const width=s?.strike-l?.strike;return {credit:credit*100*qty,maxLoss:(width-credit)*100*qty,ratio:(width-credit)/credit,breakeven:s?.strike-credit,width,takeProfitDebit:credit*.1,lossAlertDebit:credit*4};}
function validate(s,l,credit,qty,spot,under,now=Date.now()){const out=[];if(!s||!l)return ['Select both put legs'];
 for(const r of [s,l]){if(r.secType!=='OPT'||r.right!=='P'||r.currency!=='USD'||r.mult!==100||r.underConid!==under||!Number.isSafeInteger(r.conid)||r.conid<=0||r.deliverableStatus!=='IB_STANDARD_CLASS_NOT_OCC_VERIFIED')out.push('Verified standard USD 100-share put contracts required');if(!fresh(r,now))out.push('Fresh LIVE two-sided IB quotes required');if(r.quote&&r.quote.ask-r.quote.bid>.40000001)out.push('Option bid/ask spread exceeds $0.40');}
 if(s.conid===l.conid||s.root!==l.root||s.expiry!==l.expiry||!positive(l.strike)||s.strike<=l.strike||!positive(spot)||s.strike>=spot)out.push('Sell higher OTM put / buy lower put, same underlying and expiry');
 if(!(days(s.expiry,now)>=20&&days(s.expiry,now)<=45))out.push('Select 20–45 DTE');
 const delta=s.quote?.greeks?.delta;if(!finite(delta)||delta>-.09||delta<-.15)out.push('Observed short put delta must be −0.09 to −0.15');
 if(!positive(credit)||!Number.isSafeInteger(qty)||qty<=0)out.push('Positive credit / whole spread quantity required');
 const e=economics(s,l,credit,qty);if(!positive(e.maxLoss)||!finite(e.ratio)||e.ratio>10.00000001)out.push('Maximum loss must be positive and ≤10× credit');return [...new Set(out)];}
function recommend(rows,spot,now=Date.now()){rows=rows.slice().sort((a,b)=>a.strike-b.strike);const results=[];for(let i=0;i<rows.length;i++){const s=rows[i];for(const step of [1,2]){const l=rows[i-step];if(!l)continue;const credit=Math.round(((s.quote?.bid||0)-(l.quote?.ask||0))*10000)/10000;if(validate(s,l,credit,1,spot,s.underConid,now).length)continue;results.push({short:s,long:l,credit,step,buffer:1-s.strike/spot,score:Math.abs(s.quote.greeks.delta+.1)+(1-s.strike/spot<.15?.1:0)});}}return results.sort((a,b)=>a.score-b.score||a.step-b.step)[0]||null;}
return {sma,ema,metrics,screen,days,expiration,fresh,economics,validate,recommend};});
