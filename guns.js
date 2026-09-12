/* Pure GUNS rules and risk calculations; no broker order APIs. */
(function(root,f){if(typeof module==='object'&&module.exports)module.exports=f();else root.Guns=f();})(globalThis,function(){'use strict';
const VERSION='guns-1.5',defaults={riskPct:1,rewardR:2,maxSpread:.05,minVolume:30000,breakeven:true,atrPeriod:14,stopMode:'ATR',fixedStop:.2,hoverCandle:false,autoFrame:true,floatMode:'strict',maxFloat:100000000,openFrame:false,autoCharts:true};
const names={1:'Premarket high breakout',2:'Premarket pivot',3:'Premarket bull flag',4:'First opening bull flag',5:'First bullish minute'};
// Reviewed against the preserved Adam course notes; qualitative decisions remain human.
const rules=[
 {id:1,formation:'Premarket-high breakout: a rising 5-minute premarket chart consolidates just beneath its highest premarket print, ideally less than 5% below. Judge freshness of the move, extension and daily overhead resistance.',entry:'1 places a buy stop-limit at the observed premarket high + $0.01. AUTO always uses the actual high. Explicit HOVER mode can instead use your selected completed premarket candle, labeled as a user override. Arm around 09:28–09:29 ET; entry execution waits until the regular open.',stop:'Default SL distance is ATR of completed 1-minute broker candles (app default period 14). PRICE/FIXED are explicit optional presets, never an automatic fallback when ATR is missing.',target:'TP = entry + 2R or 2.5R; R = entry minus SL. Limit cap is entry + $0.03 below $20, otherwise + $0.05 (app interpretation of the source range).',invalid:'Missing premarket high, stale/invalid data, inadequate history or spread/risk failure blocks placement. Do not chase a jump above the cap. If weak after the open, cancel; app expiry is 5 minutes after open. Switching to another strategy is YOUR decision.',source:'Part II, Setup One, paragraphs 213–219. Adam emphasizes S1 as his dominant premarket setup.'},
 {id:2,formation:'After a premarket high, a pullback forms a distinct LOWER local pivot/consolidation. Price should retain moving-average support. This is not S1.',entry:'2 uses the most recent completed 5-minute lower pivot + $0.01. No pivot means no automatic placement; never substitute the premarket high.',stop:'1-minute ATR by default, or your explicitly selected price/fixed preset, below entry.',target:'2R or 2.5R. At least 1R of room from ENTRY to the premarket high is mandatory.',invalid:'Reject when the pivot is too close to overhead resistance, or data/risk guards fail. The app never changes S2 to S1 for you.',source:'Part II, Setup Two, paragraphs 239–245.'},
 {id:3,formation:'On the 5-minute premarket chart, impulse candles make higher highs, then one or more pullback candles make lower highs/inside bars. Judge support at EMA9/20 (at worst SMA50), extension and room to resistance.',entry:'3 uses the FINAL completed premarket flag candle high + $0.01, not the premarket high.',stop:'SL = that same final flag candle low - $0.01, rounded to the valid tick. No ATR fallback for a missing flag candle.',target:'2R or 2.5R. The app conservatively requires 1R of room to premarket resistance.',invalid:'If too close to the premarket high, Adam suggests considering S1; YOU must choose 1. The software will not substitute strategies. Pattern hints do not replace your support/formation judgment.',source:'Part II, Setup Three, paragraphs 249–259.'},
 {id:4,formation:'FIRST opening bull flag after 09:30 ET: rising impulse followed by completed lower-high/inside pullback candle(s), usually on 1-minute candles within the first hour. Judge EMA9/20 support and pullback no deeper than roughly 50–60%.',entry:'4 uses the latest completed OPENING flag candle high + $0.01. It never uses a premarket pivot. App uses 1-minute bars.',stop:'SL = the SAME flag candle low - $0.01. If another lower-high candle closes, app requires cancellation/review/re-arming rather than silently replacing your confirmed order.',target:'2R or 2.5R; inspect premarket/daily resistance and real Level II ask walls. Breakeven setting moves SL to actual entry at +1R, before fees.',invalid:'App enforces a maximum $0.05 spread for S4. First-flag/retracement/support interpretation remains your judgment. Source also mentions wider spread examples; app adopts the stricter S4 rule.',source:'Part II, Setup Four, paragraphs 287–299.'},
 {id:5,formation:'The very FIRST 09:30–09:31 ET one-minute candle closes bullish above the moving averages, without an unusually large range. This is not a flag strategy.',entry:'Use the S5 confirmation control, only after that first candle closes and before 09:32 ET. Entry = first candle high + $0.01.',stop:'SL = first candle low - $0.01. App range guard is at most 2x premarket ATR; the source does not specify that numerical cutoff.',target:'2R or 2.5R, with overhead resistance and spread checked.',invalid:'No late first-candle entry, no replacement by a later candle, no synthetic history. Strategy 4 remains a separate user decision.',source:'Part II, Setup Five, paragraphs 319–327.'}
];
const fmt=new Intl.DateTimeFormat('en-US',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'});
function day(t){const p=Object.fromEntries(fmt.formatToParts(new Date(t)).map(x=>[x.type,x.value]));return p.year+'-'+p.month+'-'+p.day;}
const finite=x=>typeof x==='number'&&Number.isFinite(x);
function round(p,t,up){return Math.round((up?Math.ceil(p/t-1e-8):Math.floor(p/t+1e-8))*t*1e8)/1e8;}
function average(rows,n,exp){let sum=0,v=null;return rows.map((b,i)=>{sum+=b.c;if(i>=n)sum-=rows[i-n].c;if(i<n-1)return null;v=v===null?sum/n:exp?b.c*2/(n+1)+v*(1-2/(n+1)):sum/n;return v;});}
function studies(rows){return [average(rows,9,true),average(rows,20,true),average(rows,50,false),average(rows,200,false)];}
function above(rows){return rows.length>=200&&studies(rows).every(s=>rows.at(-1).c>s.at(-1));}
function atr(rows,n){if(rows.length<n+1)return null;let values=rows.slice(1).map((b,i)=>Math.max(b.h-b.l,Math.abs(b.h-rows[i].c),Math.abs(b.l-rows[i].c)));let v=values.slice(0,n).reduce((s,x)=>s+x,0)/n;values.slice(n).forEach(x=>v=(v*(n-1)+x)/n);return v;}
function validBar(b){return b&&['o','h','l','c'].every(k=>finite(b[k])&&b[k]>0)&&b.h>=Math.max(b.o,b.c)&&b.l<=Math.min(b.o,b.c)&&b.h>=b.l;}
function aggregate(rows,n){const groups=new Map();for(const b of rows){if(!finite(b.t)||!validBar(b))continue;const t=Math.floor(b.t/(n*60000))*n*60000;if(!groups.has(t))groups.set(t,[]);groups.get(t).push(b);}
 return [...groups].sort((a,b)=>a[0]-b[0]).map(([t,rs])=>{rs.sort((a,b)=>a.t-b.t);return {t,o:rs[0].o,h:Math.max(...rs.map(b=>b.h)),l:Math.min(...rs.map(b=>b.l)),c:rs.at(-1).c,v:rs.every(b=>finite(b.v)&&b.v>=0)?rs.reduce((v,b)=>v+b.v,0):null,complete:rs.length===n&&rs.every((b,i)=>b.t===t+i*60000),observedMinutes:rs.length};});
}
function flag(rows){if(rows.length<2)return null;let i=rows.length-1;while(i>0&&rows[i].h<rows[i-1].h)i--;if(i===rows.length-1)return null;let j=i;while(j>0&&rows[j].h>rows[j-1].h)j--;const hi=rows[i].h,lo=Math.min(...rows.slice(j,i+1).map(b=>b.l));if(!(hi>lo)||j===i&&rows[i].c<=rows[i].o)return null;const low=Math.min(...rows.slice(i+1).map(b=>b.l));return {candle:rows.at(-1),low,retracement:(hi-low)/(hi-lo),first:!rows.slice(1,j+1).some((b,k)=>b.h<rows[k].h)};}
// Shared numerical placement used by the desk and isolated candle tutorial.
// This calculates levels; it does not approve chart quality or bypass execution guards.
function placement(data,cfg,setup,human){cfg=Object.assign({},defaults,cfg);setup=Number(setup);
 const {pre=[],pre5=[],regular=[],tick,atr:a}=data,pmHigh=pre.length?Math.max(...pre.map(b=>b.h)):null;
 let trigger=null,stop=setup<=2?null:undefined,pattern=null;
 if(setup===1)trigger=pmHigh;
 if(setup===2)for(let i=1;i<pre5.length-1;i++)if(pre5[i].h>=pre5[i-1].h&&pre5[i].h>pre5[i+1].h&&pre5[i].h<pmHigh)trigger=pre5[i].h;
 if(setup===3||setup===4){pattern=flag(setup===3?pre5:regular);if(pattern){trigger=pattern.candle.h;stop=pattern.candle.l-.01;}}
 if(setup===5){trigger=regular[0]?.h;stop=regular[0]?regular[0].l-.01:undefined;}
 // S1 legacy trigger overrides remain ignored; only explicit, validated hover is allowed.
 const hovered=human?.mode==='hover'&&validBar(human.candle);
 const overridden=setup>=2&&setup<=4&&finite(human?.trigger)&&human.trigger>0;
 if(hovered){trigger=human.candle.h;if(setup>=3)stop=human.candle.l-.01;}
 else if(overridden){trigger=human.trigger;if(setup>=3)stop=finite(human.candleLow)&&human.candleLow>0?human.candleLow-.01:undefined;}
 let entry=null,limit=null,target=null,risk=null;
 if(finite(trigger)&&finite(tick)&&tick>0){entry=round(trigger+.01,tick,true);limit=round(entry+(entry<20?.03:.05),tick,true);
  if(stop===null){const dist=cfg.stopMode==='FIXED'?Number(cfg.fixedStop):cfg.stopMode==='PRICE'?(entry<20?.15:entry<30?.25:entry<50?.4:.5):a;if(finite(dist)&&dist>0)stop=entry-dist;}
  if(finite(stop)){stop=round(stop,tick,false);risk=entry-stop;if(risk>0)target=round(entry+risk*cfg.rewardR,tick,true);}
 }
 return {trigger,entry,limit,stop,target,risk,pattern,pmHigh,levelSource:hovered?'hover':overridden?'user':'automatic'};
}
function analyze(data,q,cfg,setup,notes,now){cfg=Object.assign({},defaults,cfg);notes=notes||{};now=now||Date.now();setup=Number(setup);const checks=[],errors=[],advisories=[];const advise=(label,ok)=>advisories.push({label,ok:!!ok});const check=(label,ok)=>{checks.push({label,ok:!!ok});if(!ok)errors.push(label);};
 if(!data)return {checks,advisories,errors:['Waiting for live broker chart data'],setup};
 const sess=(data.sessions||[]).find(s=>day(s.start)===day(now));
 const raw=data.minute||[],observed=raw.filter(b=>finite(b.t)&&b.t<=now&&validBar(b)),closed=observed.filter(b=>b.t+60000<=now),m5=aggregate(closed,5).filter(b=>b.complete&&b.t+300000<=now);
 check('Valid ordered broker OHLC bars',raw.every((b,i)=>finite(b.t)&&validBar(b)&&(!i||b.t>raw[i-1].t)));
 check('Latest completed minute available',closed.at(-1)?.t===Math.floor(now/60000)*60000-60000);
 const pre=closed.filter(b=>sess&&b.t>=sess.start-19800000&&b.t<sess.start),regular=closed.filter(b=>sess&&b.t>=sess.start&&b.t<sess.end);
 const preObserved=observed.filter(b=>sess&&b.t>=sess.start-19800000&&b.t<sess.start);
 const daily=(data.daily||[]).filter(b=>String(b.t)<day(now)),prev=daily.at(-1)?.c,price=q&&Object.hasOwn(q,'tradeLast')?q.tradeLast:q?.last,tick=data.minTick;
 const pmHigh=preObserved.length?Math.max(...preObserved.map(b=>b.h)):null,volume=pre.length&&pre.every(b=>finite(b.v)&&b.v>=0)?pre.reduce((s,b)=>s+b.v,0):null,gap=prev&&finite(price)?(price/prev-1)*100:null;
 const period=Math.max(2,Math.min(100,Number(cfg.atrPeriod)||14)),a=atr(closed,period),preAtr=atr(pre,period);
 check('Verified penny-or-finer tick',finite(tick)&&tick>0&&tick<=.01);
 check('Corporate common stock verified',data.stockType==='COMMON'||notes.common===true);
 check('Price at least $1.50',finite(price)&&price>=1.5);check('Gap at least 5%',gap!==null&&gap>=5);
 check('Premarket volume threshold',finite(volume)&&volume>=cfg.minVolume);check('Favorable catalyst reviewed; no fixed-price buyout',notes.catalyst===true);
 check('Chart and setup reviewed by user',notes.chartSetup===setup);check('Daily overhead resistance reviewed',notes.room===true);check('Chart stream current',finite(data.updatedAt)&&data.updatedAt<=now+1000&&now-data.updatedAt<15000);
 check('Current exchange session known',!!sess);check('Spread within configured limit',q&&finite(q.bid)&&finite(q.ask)&&q.bid>0&&q.ask>=q.bid&&q.ask-q.bid<=Math.min(cfg.maxSpread,setup===4?.05:.10)+1e-9);
 const pre5=m5.filter(b=>sess&&b.t>=sess.start-19800000&&b.t<sess.start),basis=setup<=3?m5:closed;
 if(setup===2||setup===3)check('Latest completed 5-minute premarket candle available',pre5.at(-1)?.t===Math.floor(Math.min(now,sess?.start||now)/300000)*300000-300000);
 let human=notes.levels?.[setup];
 if(notes.hover?.enabled){const h=notes.hover,frame=String(h.timeframe),pool=frame==='5'?pre5:setup<=3?pre:regular,b=pool.find(b=>b.t===h.candle?.t);
  const valid=h.symbol===data.symbol&&Number(h.conid)===Number(data.conid)&&['1','5'].includes(frame)&&(setup!==3||frame==='5')&&(setup<4||frame==='1')&&b&&validBar(h.candle)&&['o','h','l','c'].every(k=>b[k]===h.candle[k])&&(setup!==5||b.t===sess?.start);
  check('Hover candle matches this contract, completed strategy timeframe and session',valid);
  human=valid?{mode:'hover',candle:h.candle}:null;
 }
 if(cfg.floatMode==='strict'){const f=data.float,t=Date.parse(f?.date),count=f?.basis==='outstanding-upper-bound'?f.upperBoundShares:f?.floatShares;check('Dated float or conservative share-count bound below cap',finite(count)&&count>0&&count<cfg.maxFloat&&typeof f.source==='string'&&finite(t)&&t<=now+86400000&&now-t<45*86400000);}
 const levels=placement({pre:preObserved,pre5,regular,tick,atr:a},cfg,setup,human);
 const {trigger,entry,limit,stop,target,risk,pattern:f}=levels;
 if(setup===1)advise('Within 5% of premarket high',pmHigh&&price>=pmHigh*.95&&price<=pmHigh*1.01);
 if(setup===2)advise('Suggested lower pivot identified',trigger!==null);
 if(setup===3||setup===4){advise('Heuristic completed lower-high bull flag',!!f);if(f){advise('Estimated retracement no deeper than 60%',f.retracement<=.6);if(setup===4)advise('Heuristic first flag',f.first);const e=average(basis,20,true).at(-1);advise('Estimated flag holds 20 EMA',finite(e)&&f.low>=e);}}
 if(setup===5){const first=regular[0];check('First completed bullish 09:30 candle only',first&&sess&&first.t===sess.start&&first.c>first.o&&now<sess.start+120000);check('Candle <=2x premarket ATR (app guardrail)',first&&preAtr&&first.h-first.l<=2*preAtr);}
 check('Above 9/20 EMA and 50/200 SMA',above(basis));
 check('Setup entry window',sess&&now>=sess.start-(setup<=3?120000:0)&&now<Math.min(sess.end,sess.start+(setup<=3?300000:setup===5?120000:3600000)));
 check('Valid trigger, stop and target',entry>0&&stop>0&&risk>0&&target>entry);
 if(setup!==1)check('At least 1R before premarket resistance',entry&&pmHigh&&(setup===2?pmHigh-entry>=risk:entry>pmHigh||pmHigh-entry>=risk));
 check('No chase above entry limit',limit&&q?.ask<=limit);
 return {setup,checks,errors,advisories,levelSource:levels.levelSource,entry,limit,stop,target,risk,atr:a,pmHigh,gap,volume,session:sess,
 expiresAt:sess?Math.min(sess.end,sess.start+(setup<=3?300000:setup===5?120000:3600000)):null};
}
function size(equity,pct,entry,stop,bp,fees){const budget=equity*pct/100,d=entry-stop;const zero={equity,budget:finite(budget)?budget:0,qty:0,risk:0,fees:0,unused:finite(budget)?budget:0};if(![equity,pct,entry,stop,bp].every(finite)||equity<=0||pct<=0||pct>100||entry<=0||stop<=0||d<=0||bp<=0)return zero;fees=fees||(()=>0);let lo=0,hi=Math.floor(Math.min(budget/d,bp/entry));while(lo<hi){const n=Math.ceil((lo+hi)/2),f=fees(n);if(finite(f)&&f>=0&&n*d+f<=budget+1e-8&&n*entry+f<=bp+1e-8)lo=n;else hi=n-1;}const f=lo?fees(lo):0;return {equity,budget,qty:lo,risk:lo*d+f,fees:f,unused:budget-lo*d-f};}
function exit(b,q,now){if(b.forceExit||b.stopTriggered||q.bid<=b.stop)return {reason:b.forceExit?'MANUAL FLATTEN':'STOP',stop:b.stop};if(now>=b.sessionEnd-60000)return {reason:'SESSION CLOSE',stop:b.stop};if(q.bid>=b.target)return {reason:'TARGET',stop:b.stop};return {reason:null,stop:b.breakeven&&q.bid>=b.entry+b.initialR?Math.max(b.stop,b.entry):b.stop};}
return {VERSION,defaults,names,rules,validBar,day,round,average,studies,atr,aggregate,flag,placement,analyze,size,exit};
});
