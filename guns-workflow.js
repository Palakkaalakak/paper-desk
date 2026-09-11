/* User-led review helpers. Ranking is attention guidance, not a return forecast. */
(function(r,f){if(typeof module==='object'&&module.exports)module.exports=f();else r.GunsWorkflow=f();})(globalThis,function(){'use strict';
 const defaults=['1','2','3','4'];
 function chord(e){if(e.ctrlKey||e.metaKey||e.shiftKey||e.repeat||e.isComposing)return null;const code=e.code||'';if(!e.altKey&&/^Digit[1-4]$/.test(code))return code.slice(5);if(e.altKey&&/^(Key[A-Z]|Digit[0-9])$/.test(code))return 'Alt+'+code.replace('Key','').replace('Digit','');return null;}
 function bindings(c){const b=c?.shortcuts;return Array.isArray(b)&&b.length===4&&new Set(b).size===4&&b.every(x=>/^(?:[1-4]|Alt\+[A-Z0-9])$/.test(x))?b.slice():defaults.slice();}
 function setBinding(current,index,value){if(!Number.isInteger(index)||index<0||index>3||!/^(?:[1-4]|Alt\+[A-Z0-9])$/.test(value||'')||current.some((x,i)=>i!==index&&x===value))return null;const next=current.slice();next[index]=value;return next;}
 function candidate(row,q,evidence,ready,cfg,now){const why=[],e=evidence||{},finite=Number.isFinite;
  if(!ready||q?.status!=='LIVE')why.push('fresh LIVE quote missing');
  if(!evidence||!finite(e.at)||now-e.at>90000||e.at>now+5000)why.push('verification missing/stale');
  if(Number(e.conid)!==Number(row.conid))why.push('contract identity unverified');
  if(!e.sessionKnown)why.push('session unknown');if(e.stockType!=='COMMON')why.push('common-stock classification unverified');
  if(!(q?.last>=1.5))why.push('price below $1.50 or unknown');
  const gap=finite(e.previousClose)&&e.previousClose>0&&finite(q?.last)?100*(q.last/e.previousClose-1):null;
  if(gap===null||gap<5)why.push('5% gap not verified');
  if(!finite(e.premarketVolume)||e.premarketVolume<cfg.minVolume)why.push('premarket volume below minimum or unknown');
  const spread=finite(q?.ask)&&finite(q?.bid)&&q.bid>0&&q.ask>=q.bid?q.ask-q.bid:null;
  if(spread===null||spread>cfg.maxSpread+1e-8)why.push('spread too wide or unknown');
  const score=why.length?null:Math.round(Math.min(40,10*Math.log10(Math.max(1,e.premarketVolume/30000)))+Math.min(30,gap)+30*Math.max(0,1-spread/cfg.maxSpread));
  return {row,gap,spread,volume:e.premarketVolume,why,score,eligible:why.length===0};
 }
 function rank(rows,quotes,evidence,ready,cfg,now){return rows.map(r=>candidate(r,quotes[r.conid],evidence.get(r.conid),ready(r.conid),cfg,now)).sort((a,b)=>Number(b.eligible)-Number(a.eligible)||(b.score??-1)-(a.score??-1)||(a.row.rank??999)-(b.row.rank??999));}
 return {defaults,chord,bindings,setBinding,candidate,rank};
});
