/* Pure GUNS rules and risk calculations; no broker order APIs. */
(function(root,f){if(typeof module==='object'&&module.exports)module.exports=f();else root.Guns=f();})(globalThis,function(){'use strict';
const VERSION='guns-1.5',defaults={riskPct:1,rewardR:2,maxSpread:.05,minVolume:30000,breakeven:false,atrPeriod:14,stopMode:'ATR',fixedStop:.2,hoverCandle:false,autoFrame:true,floatMode:'strict',maxFloat:100000000,openFrame:false,autoCharts:true,l2Enabled:true,l2AutoCancel:false,l2AskRatio:3,l2WallRatio:4};
const names={1:'Premarket high breakout',2:'Premarket pivot',3:'Premarket bull flag',4:'First opening bull flag',5:'First bullish minute'};
// Reviewed against the preserved Adam course notes; qualitative decisions remain human.
const rules=[
 {id:1,formation:'Premarket-high breakout: a rising 5-minute premarket chart consolidates just beneath its highest premarket print, ideally less than 5% below. Judge freshness of the move, extension and daily overhead resistance.',entry:'1 previews a buy stop-limit at the observed premarket high + $0.01; Enter queues the paper order. AUTO always uses the actual high. Explicit HOVER mode can instead use your selected completed premarket candle, labeled as a user override. Queue whenever the chart levels are calculable in premarket; execution waits until the regular open.',stop:'Default SL distance is ATR of completed 1-minute broker candles (app default period 14). PRICE/FIXED are explicit optional presets, never an automatic fallback when ATR is missing.',target:'TP = entry + 2R or 2.5R; R = entry minus SL. Limit cap is entry + $0.03 below $20, otherwise + $0.05 (app interpretation of the source range).',invalid:'Missing price references, invalid numbers or uncalculable sizing need data recovery. Screening warnings are advisory. Confirmed orders wait below their limit cap without chasing and expire at regular-session close unless cancelled or filled. Switching to another strategy is YOUR decision.',source:'Part II, Setup One, paragraphs 213–219. Adam emphasizes S1 as his dominant premarket setup.'},
 {id:2,formation:'After a premarket high, a pullback forms a distinct LOWER local pivot/consolidation. Price should retain moving-average support. This is not S1.',entry:'2 uses the most recent completed 5-minute lower pivot + $0.01. No pivot means no automatic placement; never substitute the premarket high.',stop:'1-minute ATR by default, or your explicitly selected price/fixed preset, below entry.',target:'2R or 2.5R. At least 1R of room from ENTRY to the premarket high is a screening recommendation, not a placement veto.',invalid:'Review overhead resistance; it does not force manual price entry. A real completed pivot and calculable stop/sizing are required. The app never changes S2 to S1 for you.',source:'Part II, Setup Two, paragraphs 239–245.'},
 {id:3,formation:'On the 5-minute premarket chart, impulse candles make higher highs, then one or more pullback candles make lower highs/inside bars. Judge support at EMA9/20 (at worst SMA50), extension and room to resistance.',entry:'3 uses the FINAL completed premarket flag candle high + $0.01, not the premarket high.',stop:'SL = that same final flag candle low - $0.01, rounded to the valid tick. No ATR fallback for a missing flag candle.',target:'2R or 2.5R. The checklist recommends 1R of room to premarket resistance; confirmed paper orders are not vetoed by that assessment.',invalid:'If too close to the premarket high, Adam suggests considering S1; YOU must choose 1. The software will not substitute strategies. Pattern hints do not replace your support/formation judgment.',source:'Part II, Setup Three, paragraphs 249–259.'},
 {id:4,formation:'FIRST opening bull flag after 09:30 ET: rising impulse followed by completed lower-high/inside pullback candle(s), usually on 1-minute candles within the first hour. Judge EMA9/20 support and pullback no deeper than roughly 50–60%.',entry:'4 uses the latest completed OPENING flag candle high + $0.01. It never uses a premarket pivot. App uses 1-minute bars.',stop:'SL = the SAME flag candle low - $0.01. Confirmed prices remain fixed as later candles close. Cancel and preview again if you want new prices.',target:'2R or 2.5R; inspect premarket/daily resistance and real Level II ask walls. Breakeven setting moves SL to actual entry at +1R, before fees.',invalid:'App enforces a maximum $0.05 spread for S4. First-flag/retracement/support interpretation remains your judgment. Source also mentions wider spread examples; app adopts the stricter S4 rule.',source:'Part II, Setup Four, paragraphs 287–299.'},
 {id:5,formation:'The very FIRST 09:30–09:31 ET one-minute candle closes bullish above the moving averages, without an unusually large range. This is not a flag strategy.',entry:'Use the S5 confirmation control, only after that first candle closes and before 09:32 ET. Entry = first candle high + $0.01.',stop:'SL = first candle low - $0.01. App range guard is at most 2x premarket ATR; the source does not specify that numerical cutoff.',target:'2R or 2.5R, with overhead resistance and spread checked.',invalid:'No late first-candle entry, no replacement by a later candle, no synthetic history. Strategy 4 remains a separate user decision.',source:'Part II, Setup Five, paragraphs 319–327.'}
];
const fmt=new Intl.DateTimeFormat('en-US',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'});
function day(t){const p=Object.fromEntries(fmt.formatToParts(new Date(t)).map(x=>[x.type,x.value]));return p.year+'-'+p.month+'-'+p.day;}
const finite=x=>typeof x==='number'&&Number.isFinite(x);
// One calculation for scanner and planner. No midpoint, undated quote.close,
// weekday calendar guesses, or older daily-bar substitutions.
function gapEvidence(q,e,now){
 const fail=reason=>({value:null,reason});e=e||{};
 const today=day(now),date=e.previousCloseDate,expected=e.expectedPreviousCloseDate,close=e.previousClose;
 if(e.sessionDate!==today||!e.previousCloseVerified||!finite(close)||close<=0||!/^\d{4}-\d{2}-\d{2}$/.test(date||'')||date!==expected||date>=today||!e.previousCloseSource||e.previousCloseBasis!=='split-adjusted-not-dividend-adjusted')return fail('Verified prior trading-session close unavailable');
 if(!finite(e.previousCloseAt)||now-e.previousCloseAt<0||now-e.previousCloseAt>90000)return fail('Prior-close evidence needs refresh');
 let price=q&&Object.hasOwn(q,'tradeLast')?q.tradeLast:q?.last,at=q?.tradeAt,basis='timestamped last trade';
 const live=q?.status==='LIVE'&&!q.error&&!q.halted&&finite(q.at)&&now-q.at>=0&&now-q.at<15000;
 if(!live)return fail('Fresh live quote unavailable');
 if(!finite(price)||price<=0||!finite(at)||now-at<0||now-at>60000||day(at)!==today){
  const b=e.recentTradeBar;
  if(!b||!finite(b.price)||b.price<=0||!finite(b.start)||!finite(b.end)||b.end-b.start!==60000||now-b.end<0||now-b.end>60000||day(b.start)!==today)return fail('Timestamped trade or recent completed trade bar unavailable');
  price=b.price;at=b.end;basis='completed 1m TRADES close (not a live trade)';
 }
 const value=100*(price-close)/close;
 if(!finite(value))return fail('Gap arithmetic unavailable');
 return {value,price,at,basis,previousClose:close,previousCloseDate:date,previousCloseSource:e.previousCloseSource,adjustment:e.previousCloseBasis,reason:null};
}
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
 if(!data)return {checks,advisories,errors:['Waiting for broker chart data'],calculationErrors:['Broker chart candles not loaded'],setup};
 const sess=(data.sessions||[]).find(s=>day(s.start)===day(now));
 const raw=data.minute||[],observed=raw.filter(b=>finite(b.t)&&b.t<=now&&validBar(b)),closed=observed.filter(b=>b.t+60000<=now),m5=aggregate(closed,5).filter(b=>b.complete&&b.t+300000<=now);
 check('Valid ordered broker OHLC bars',raw.every((b,i)=>finite(b.t)&&validBar(b)&&(!i||b.t>raw[i-1].t)));
 check('Latest completed minute available',closed.at(-1)?.t===Math.floor(now/60000)*60000-60000);
 const pre=closed.filter(b=>sess&&b.t>=sess.start-19800000&&b.t<sess.start),regular=closed.filter(b=>sess&&b.t>=sess.start&&b.t<sess.end);
 const preObserved=observed.filter(b=>sess&&b.t>=sess.start-19800000&&b.t<sess.start);
 const price=q&&Object.hasOwn(q,'tradeLast')?q.tradeLast:q?.last,tick=data.minTick;
 const pmHigh=preObserved.length?Math.max(...preObserved.map(b=>b.h)):null,volume=pre.length&&pre.every(b=>finite(b.v)&&b.v>=0)?pre.reduce((s,b)=>s+b.v,0):null,gapInfo=gapEvidence(q,data,now),gap=gapInfo.value;
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
 // Screening judgments and quote freshness do not prevent queuing a chart-derived paper order.
 const calculationErrors=[];
 if(!checks.find(c=>c.label==='Valid ordered broker OHLC bars')?.ok)calculationErrors.push('Invalid or unordered chart candles');
 if(!finite(tick)||tick<=0)calculationErrors.push('Contract price increment unavailable');
 if(!sess||!finite(sess.start)||!finite(sess.end)||sess.end<=sess.start||now>=sess.end)calculationErrors.push('Current regular-market session unavailable or already closed');
 if(notes.hover?.enabled&&!checks.find(c=>c.label.startsWith('Hover candle matches'))?.ok)calculationErrors.push('Selected hover candle does not match the stock / strategy');
 if(!finite(trigger)||trigger<=0)calculationErrors.push(['','Premarket high unavailable','No completed lower premarket pivot found','No completed premarket flag candle found','No completed opening flag candle found','First regular-market minute has not completed'][setup]||'Unknown strategy');
 if(setup===5&&regular[0]?.t!==sess?.start)calculationErrors.push('First regular-market minute is missing; later candles cannot replace it');
 if(![entry,limit,stop,target].every(finite)||!(stop>0&&entry>stop&&limit>=entry&&target>entry))calculationErrors.push(setup<=2&&cfg.stopMode==='ATR'&&!finite(a)?'Not enough completed minute candles to calculate ATR':'Cannot calculate valid entry, stop and target from the strategy candles');
 return {setup,checks,errors,advisories,calculationErrors,chartAt:data.updatedAt,chartSymbol:data.symbol,chartConid:data.conid,tick,levelSource:levels.levelSource,trigger,pattern:f,firstCandle:regular.find(b=>b.t===sess?.start),preAtr,
 contextCurrent:checks.filter(c=>['Valid ordered broker OHLC bars','Latest completed minute available','Chart stream current','Current exchange session known'].includes(c.label)).every(c=>c.ok),
 entry,limit,stop,target,risk,atr:a,pmHigh,gap,gapInfo,volume,session:sess,
 expiresAt:sess?Math.min(sess.end,sess.start+(setup<=3?300000:setup===5?120000:3600000)):null};
}
// Informational context only: uses the same analyzed levels as execution, never changes orders.
function strategyHint(p,q,quoteReady,now){
 const setup=Number(p.setup),rule=rules.find(r=>r.id===setup),title='S'+setup+' · '+(names[setup]||'Select a strategy');
 const price=q&&Object.hasOwn(q,'tradeLast')?q.tradeLast:q?.last;
 const live=quoteReady&&finite(price)&&price>0,usd=v=>'$'+v.toFixed(2);
 const details=[rule?.formation||'Select S1–S5 for context.'];
 const distance=(level,label)=>{if(!finite(level)||level<=0)return label+' unavailable — waiting for a valid reference.';
  if(!live)return label+' '+usd(level)+' · distance unavailable (live trade quote required).';
  const delta=price-level,relation=Math.abs(delta)<1e-8?'at':delta<0?'below':'above';
  return 'Last '+usd(price)+' is '+(relation==='at'?'at':usd(Math.abs(delta))+' ('+(Math.abs(delta)/level*100).toFixed(2)+'%) '+relation)+' '+label+' '+usd(level)+'.';};
 let summary='Live context unavailable — chart/session data is missing, stale or mismatched.';
 if(p.contextCurrent){
  const labels={1:'premarket high',2:'lower pivot',3:'final premarket flag high',4:'opening flag high',5:'first 09:30 candle high'};
  const level=setup===1?p.pmHigh:setup===5?p.firstCandle?.h:p.trigger,label=p.levelSource==='hover'?'hover candle high':p.levelSource==='user'?'manual trigger':labels[setup];
  summary=distance(level,setup===1?'premarket high':label);
  if(setup===1&&live&&finite(p.pmHigh)&&p.pmHigh>0)details.push(price<=p.pmHigh&&price>=p.pmHigh*.95?'Within the 5% preparation zone below PM high; this does not confirm a breakout.':price>p.pmHigh?'Above PM high; inspect the entry limit before acting.':'More than 5% below PM high.');
  if(p.levelSource==='hover'||p.levelSource==='user')details.push('Entry reference: '+p.levelSource+' override. '+distance(p.trigger,label)+ ' Actual PM high remains '+(finite(p.pmHigh)?usd(p.pmHigh):'unavailable')+'.');
  if(setup===2||setup===3){const room=p.pmHigh-p.entry;details.push(finite(p.pmHigh)&&finite(p.entry)&&finite(p.risk)&&p.risk>0?(room>=0?'Room from entry to PM high: '+usd(room)+' / '+(room/p.risk).toFixed(2)+'R (1R minimum).':'Entry is above PM high; it is not overhead resistance at this entry.'):'Room to PM high unavailable — valid entry, risk and PM high required.');}
  if(setup===3||setup===4)details.push(p.pattern&&finite(p.pattern.retracement)?'Automatic flag estimate: '+(p.pattern.retracement*100).toFixed(1)+'% retracement; '+(p.pattern.first?'first detected flag.':'not the first detected flag.')+' Heuristic only; an override may use a different candle.':'No automatic completed flag detected; any explicit override still needs review.');
  if(setup===5){const first=p.firstCandle;details.push(validBar(first)?'First minute closed '+(first.c>first.o?'bullish':'not bullish')+'; range '+usd(first.h-first.l)+(finite(p.preAtr)&&p.preAtr>0?' / '+((first.h-first.l)/p.preAtr).toFixed(2)+'× premarket ATR (maximum 2×).':'; premarket ATR unavailable.'):'Waiting for the completed 09:30–09:31 ET candle; later candles never substitute.');
   if(finite(p.session?.start)&&finite(now)){const opens=p.session.start+60000,ends=p.session.start+120000;details.push(now<opens?'First-candle close in '+Math.ceil((opens-now)/1000)+'s.':now<ends?'S5 entry window closes in '+Math.ceil((ends-now)/1000)+'s.':'S5 entry window closed at 09:32 ET.');}}
  details.push(distance(p.entry,'planned entry'));
  if(live&&finite(q?.ask)&&q.ask>0&&finite(p.limit))details.push(q.ask>p.limit?'Ask is '+usd(q.ask-p.limit)+' above limit cap '+usd(p.limit)+' — no chase.':'Ask-to-limit headroom: '+usd(p.limit-q.ask)+' (cap '+usd(p.limit)+').');
 }
 details.push(rule?.entry||'',rule?.stop||'','Screening information is advisory. Chart references calculate the order; live quotes are needed for a paper fill, not premarket queuing.');
 return {title,summary,details:details.filter(Boolean)};
}
function depthRisk(d,inst,p,cfg,now){
 const invalid=reason=>({valid:false,reason,flags:[],key:''});
 if(!d||d.status!=='LIVE'||d.source!=='IB Gateway SMART depth'||Number(d.conid)!==Number(inst.conid))return invalid('Awaiting matching LIVE IBKR Level II');
 if(!finite(d.updatedAt)||now-d.updatedAt<0||now-d.updatedAt>5000||!Number.isInteger(d.revision)||d.revision<1)return invalid('Level II stale or awaiting depth update');
 const good=x=>x&&finite(x.price)&&x.price>0&&finite(x.size)&&x.size>0;
 if(!Array.isArray(d.bids)||!Array.isArray(d.asks)||d.bids.length<3||d.asks.length<3||!d.bids.every(good)||!d.asks.every(good))return invalid('Need three valid displayed levels on each side');
 const bids=[...d.bids].sort((a,b)=>b.price-a.price).slice(0,5),asks=[...d.asks].sort((a,b)=>a.price-b.price).slice(0,5);
 if(bids[0].price>asks[0].price)return invalid('Depth book crossed/resetting');
 const bidSize=bids.reduce((s,x)=>s+x.size,0),askSize=asks.reduce((s,x)=>s+x.size,0),ratio=askSize/bidSize,spread=asks[0].price-bids[0].price,flags=[];
 if(spread>Math.min(.05,Number(cfg.maxSpread)||.05)+1e-8)flags.push({code:'spread',text:'Displayed spread $'+spread.toFixed(4)+' exceeds limit'});
 if(ratio>=Math.max(1.5,Number(cfg.l2AskRatio)||3))flags.push({code:'imbalance',text:'Top-five ask size is '+ratio.toFixed(2)+'× bid size'});
 const sizes=bids.map(x=>x.size).sort((a,b)=>a-b),median=sizes[Math.floor(sizes.length/2)],risk=p.entry-p.stop;
 const wall=asks.find(x=>risk>0&&x.price>=p.entry&&x.price<=p.entry+.5*risk&&x.size>=median*Math.max(2,Number(cfg.l2WallRatio)||4));
 if(wall)flags.push({code:'wall',text:'Nearby ask wall at $'+wall.price.toFixed(4)+' · '+(wall.size/median).toFixed(2)+'× median bid level'});
 return {valid:true,flags,key:flags.map(f=>f.code).sort().join('|'),at:d.updatedAt,revision:d.revision,stream:d.stream,metrics:{bidSize,askSize,ratio,spread},reason:flags.length?'Adverse displayed depth':'No configured depth flag'};
}
function premarketBands(rows,frame,sessions){if(frame==='d')return [];const duration=Number(frame)*60000;if(!finite(duration)||duration<=0)return [];const clock=new Intl.DateTimeFormat('en-US',{timeZone:'America/New_York',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}),windows=(sessions||[]).filter(s=>finite(s.start)).map(s=>{const parts=Object.fromEntries(clock.formatToParts(new Date(s.start)).map(p=>[p.type,p.value]));return {start:s.start-((Number(parts.hour)-4)*60+Number(parts.minute))*60000,end:s.start};});return rows.flatMap((b,index)=>{if(!finite(b.t))return [];const s=windows.find(s=>b.t<s.end&&b.t+duration>s.start);if(!s)return [];return [{index,from:Math.max(0,(s.start-b.t)/duration),to:Math.min(1,(s.end-b.t)/duration)}];});}
function size(equity,pct,entry,stop,bp,fees){const budget=equity*pct/100,d=entry-stop;const zero={equity,budget:finite(budget)?budget:0,qty:0,risk:0,fees:0,unused:finite(budget)?budget:0};if(![equity,pct,entry,stop,bp].every(finite)||equity<=0||pct<=0||pct>100||entry<=0||stop<=0||d<=0||bp<=0)return zero;fees=fees||(()=>0);let lo=0,hi=Math.floor(Math.min(budget/d,bp/entry));while(lo<hi){const n=Math.ceil((lo+hi)/2),f=fees(n);if(finite(f)&&f>=0&&n*d+f<=budget+1e-8&&n*entry+f<=bp+1e-8)lo=n;else hi=n-1;}const f=lo?fees(lo):0;return {equity,budget,qty:lo,risk:lo*d+f,fees:f,unused:budget-lo*d-f};}
function exit(b,q,now){if(b.forceExit||b.stopTriggered||q.bid<=b.stop)return {reason:b.forceExit?'MANUAL FLATTEN':'STOP',stop:b.stop};if(finite(b.sessionEnd)&&now>=b.sessionEnd-60000)return {reason:'SESSION CLOSE',stop:b.stop};if(q.bid>=b.target)return {reason:'TARGET',stop:b.stop};return {reason:null,stop:b.breakeven&&q.bid>=b.entry+b.initialR?Math.max(b.stop,b.entry):b.stop};}
return {VERSION,defaults,names,rules,validBar,day,gapEvidence,round,average,studies,atr,aggregate,flag,placement,analyze,strategyHint,depthRisk,premarketBands,size,exit};
});
