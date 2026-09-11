/* Pure GUNS rules and risk calculations; no broker order APIs. */
(function(root,f){if(typeof module==='object'&&module.exports)module.exports=f();else root.Guns=f();})(globalThis,function(){'use strict';
const VERSION='guns-1.2',defaults={riskPct:1,rewardR:2,maxSpread:.05,minVolume:30000,breakeven:true,atrPeriod:14,stopMode:'ATR',fixedStop:.2};
const names={1:'Premarket high breakout',2:'Premarket pivot',3:'Premarket bull flag',4:'First opening bull flag',5:'First bullish minute'};
const fmt=new Intl.DateTimeFormat('en-US',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'});
function day(t){const p=Object.fromEntries(fmt.formatToParts(new Date(t)).map(x=>[x.type,x.value]));return p.year+'-'+p.month+'-'+p.day;}
const finite=x=>typeof x==='number'&&Number.isFinite(x);
function round(p,t,up){return Math.round((up?Math.ceil(p/t-1e-8):Math.floor(p/t+1e-8))*t*1e8)/1e8;}
function average(rows,n,exp){let sum=0,v=null;return rows.map((b,i)=>{sum+=b.c;if(i>=n)sum-=rows[i-n].c;if(i<n-1)return null;v=v===null?sum/n:exp?b.c*2/(n+1)+v*(1-2/(n+1)):sum/n;return v;});}
function studies(rows){return [average(rows,9,true),average(rows,20,true),average(rows,50,false),average(rows,200,false)];}
function above(rows){return rows.length>=200&&studies(rows).every(s=>rows.at(-1).c>s.at(-1));}
function atr(rows,n){if(rows.length<n+1)return null;let values=rows.slice(1).map((b,i)=>Math.max(b.h-b.l,Math.abs(b.h-rows[i].c),Math.abs(b.l-rows[i].c)));let v=values.slice(0,n).reduce((s,x)=>s+x,0)/n;values.slice(n).forEach(x=>v=(v*(n-1)+x)/n);return v;}
function aggregate(rows,n){const m=new Map();rows.forEach(b=>{const t=Math.floor(b.t/(n*60000))*n*60000;let x=m.get(t);if(!x){x={t,o:b.o,h:b.h,l:b.l,c:b.c,v:0};m.set(t,x);}x.h=Math.max(x.h,b.h);x.l=Math.min(x.l,b.l);x.c=b.c;x.v+=b.v||0;});return Array.from(m.values());}
function flag(rows){if(rows.length<2)return null;let i=rows.length-1;while(i>0&&rows[i].h<rows[i-1].h)i--;if(i===rows.length-1)return null;let j=i;while(j>0&&rows[j].h>rows[j-1].h)j--;const hi=rows[i].h,lo=Math.min(...rows.slice(j,i+1).map(b=>b.l));if(!(hi>lo)||j===i&&rows[i].c<=rows[i].o)return null;const low=Math.min(...rows.slice(i+1).map(b=>b.l));return {candle:rows.at(-1),low,retracement:(hi-low)/(hi-lo),first:!rows.slice(1,j+1).some((b,k)=>b.h<rows[k].h)};}
// Shared numerical placement used by the desk and isolated candle tutorial.
// This calculates levels; it does not approve chart quality or bypass execution guards.
function placement(data,cfg,setup,human){cfg=Object.assign({},defaults,cfg);setup=Number(setup);
 const {pre=[],pre5=[],regular=[],tick,atr:a}=data,pmHigh=pre.length?Math.max(...pre.map(b=>b.h)):null;
 let trigger=null,stop=setup<=2?null:undefined,pattern=null;
 if(setup===1)trigger=pmHigh;
 if(setup===2)for(let i=1;i<pre5.length-1;i++)if(pre5[i].h>=pre5[i-1].h&&pre5[i].h>pre5[i+1].h&&pre5[i].h<pmHigh)trigger=pre5[i].h;
 if(setup===3||setup===4){pattern=flag(setup===3?pre5:regular);if(pattern){trigger=pattern.candle.h;stop=pattern.candle.l-tick;}}
 if(setup===5){trigger=regular[0]?.h;stop=regular[0]?regular[0].l-tick:undefined;}
 const overridden=setup<=4&&finite(human?.trigger)&&human.trigger>0;
 if(overridden){trigger=human.trigger;if(setup>=3)stop=finite(human.candleLow)&&human.candleLow>0?human.candleLow-tick:undefined;}
 let entry=null,limit=null,target=null,risk=null;
 if(finite(trigger)&&finite(tick)&&tick>0){entry=round(trigger+tick,tick,true);limit=round(entry+(entry<20?.03:.05),tick,true);
  if(stop===null){const dist=cfg.stopMode==='FIXED'?Number(cfg.fixedStop):cfg.stopMode==='PRICE'?(entry<20?.15:entry<30?.25:entry<50?.4:.5):a;if(finite(dist)&&dist>0)stop=entry-dist;}
  if(finite(stop)){stop=round(stop,tick,false);risk=entry-stop;if(risk>0)target=round(entry+risk*cfg.rewardR,tick,true);}
 }
 return {trigger,entry,limit,stop,target,risk,pattern,pmHigh,levelSource:overridden?'user':'automatic'};
}
function analyze(data,q,cfg,setup,notes,now){cfg=Object.assign({},defaults,cfg);notes=notes||{};now=now||Date.now();setup=Number(setup);const checks=[],errors=[],advisories=[];const advise=(label,ok)=>advisories.push({label,ok:!!ok});const check=(label,ok)=>{checks.push({label,ok:!!ok});if(!ok)errors.push(label);};
 if(!data)return {checks,advisories,errors:['Waiting for live broker chart data'],setup};
 const sess=(data.sessions||[]).find(s=>day(s.start)===day(now));
 const closed=(data.minute||[]).filter(b=>finite(b.t)&&b.t+60000<=now),m5=aggregate(closed,5).filter(b=>b.t+300000<=now);
 const pre=closed.filter(b=>sess&&b.t>=sess.start-19800000&&b.t<sess.start),regular=closed.filter(b=>sess&&b.t>=sess.start&&b.t<sess.end);
 const daily=(data.daily||[]).filter(b=>String(b.t)<day(now)),prev=daily.at(-1)?.c,price=q?.last,tick=data.minTick;
 const pmHigh=pre.length?Math.max(...pre.map(b=>b.h)):null,volume=pre.reduce((s,b)=>s+(b.v||0),0),gap=prev&&finite(price)?(price/prev-1)*100:null;
 const period=Math.max(2,Math.min(100,Number(cfg.atrPeriod)||14)),a=atr(closed,period),preAtr=atr(pre,period);
 check('Verified penny-or-finer tick',finite(tick)&&tick>0&&tick<=.01);
 check('Corporate common stock verified',data.stockType==='COMMON'||notes.common===true);
 check('Price at least $1.50',finite(price)&&price>=1.5);check('Gap at least 5%',gap!==null&&gap>=5);
 check('Premarket volume threshold',volume>=cfg.minVolume);check('Favorable catalyst reviewed; no fixed-price buyout',notes.catalyst===true);
 check('Chart and setup reviewed by user',notes.chartSetup===setup);check('Daily overhead resistance reviewed',notes.room===true);check('Chart stream current',finite(data.updatedAt)&&now-data.updatedAt<90000);
 check('Current exchange session known',!!sess);check('Spread within configured limit',q&&finite(q.bid)&&finite(q.ask)&&q.bid>0&&q.ask>=q.bid&&q.ask-q.bid<=cfg.maxSpread+1e-9);
 const pre5=m5.filter(b=>sess&&b.t>=sess.start-19800000&&b.t<sess.start),basis=setup<=3?m5:closed;
 const levels=placement({pre,pre5,regular,tick,atr:a},cfg,setup,notes.levels?.[setup]);
 const {trigger,entry,limit,stop,target,risk,pattern:f}=levels;
 if(setup===1)advise('Within 5% of premarket high',pmHigh&&price>=pmHigh*.95&&price<=pmHigh*1.01);
 if(setup===2)advise('Suggested lower pivot identified',trigger!==null);
 if(setup===3||setup===4){advise('Heuristic completed lower-high bull flag',!!f);if(f){advise('Estimated retracement no deeper than 60%',f.retracement<=.6);if(setup===4)advise('Heuristic first flag',f.first);const e=average(basis,20,true).at(-1);advise('Estimated flag holds 20 EMA',finite(e)&&f.low>=e);}}
 if(setup===5){const first=regular[0];check('First completed bullish 09:30 candle only',first&&sess&&first.t===sess.start&&first.c>first.o&&now<sess.start+120000);check('Candle <=2x premarket ATR (app guardrail)',first&&preAtr&&first.h-first.l<=2*preAtr);}
 check('Above 9/20 EMA and 50/200 SMA',above(basis));
 check('Setup entry window',sess&&now>=sess.start-(setup<=3?120000:0)&&now<Math.min(sess.end,sess.start+(setup<=3?300000:setup===5?120000:3600000)));
 check('Valid trigger, stop and target',entry>0&&stop>0&&risk>0&&target>entry);
 if(setup!==1)check('At least 1R before premarket resistance',entry&&pmHigh&&(entry>pmHigh||pmHigh-entry>=risk));
 check('No chase above entry limit',limit&&q?.ask<=limit);
 return {setup,checks,errors,advisories,levelSource:levels.levelSource,entry,limit,stop,target,risk,atr:a,pmHigh,gap,volume,session:sess,
 expiresAt:sess?Math.min(sess.end,sess.start+(setup<=3?300000:setup===5?120000:3600000)):null};
}
function size(equity,pct,entry,stop,bp,fees){const budget=equity*pct/100,d=entry-stop;const zero={equity,budget:finite(budget)?budget:0,qty:0,risk:0,fees:0,unused:finite(budget)?budget:0};if(![equity,pct,entry,stop,bp].every(finite)||equity<=0||pct<=0||pct>100||entry<=0||stop<=0||d<=0||bp<=0)return zero;fees=fees||(()=>0);let lo=0,hi=Math.floor(Math.min(budget/d,bp/entry));while(lo<hi){const n=Math.ceil((lo+hi)/2),f=fees(n);if(finite(f)&&f>=0&&n*d+f<=budget+1e-8&&n*entry+f<=bp+1e-8)lo=n;else hi=n-1;}const f=lo?fees(lo):0;return {equity,budget,qty:lo,risk:lo*d+f,fees:f,unused:budget-lo*d-f};}
function exit(b,q,now){if(b.forceExit||b.stopTriggered||q.bid<=b.stop)return {reason:b.forceExit?'MANUAL FLATTEN':'STOP',stop:b.stop};if(now>=b.sessionEnd-60000)return {reason:'SESSION CLOSE',stop:b.stop};if(q.bid>=b.target)return {reason:'TARGET',stop:b.stop};return {reason:null,stop:b.breakeven&&q.bid>=b.entry+b.initialR?Math.max(b.stop,b.entry):b.stop};}
return {VERSION,defaults,names,day,round,average,studies,atr,aggregate,flag,placement,analyze,size,exit};
});
