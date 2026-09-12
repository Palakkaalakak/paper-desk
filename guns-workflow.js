/* User-led screening and keyboard helpers; no chart approval or broker orders. */
(function(r,f){if(typeof module==='object'&&module.exports)module.exports=f();else r.GunsWorkflow=f();})(globalThis,function(){'use strict';
 const defaults=['1','2','3','4','5'];
 const valid=x=>/^(?:[1-5]|Alt\+[A-Z0-9])$/.test(x);
 function chord(e){if(e.ctrlKey||e.metaKey||e.shiftKey||e.repeat||e.isComposing)return null;const code=e.code||'';if(!e.altKey&&/^Digit[1-5]$/.test(code))return code.slice(5);if(e.altKey&&/^(Key[A-Z]|Digit[0-9])$/.test(code))return 'Alt+'+code.replace('Key','').replace('Digit','');return null;}
 function bindings(c){const b=c?.shortcuts;if(!Array.isArray(b)||![4,5].includes(b.length)||new Set(b).size!==b.length||!b.every(valid))return defaults.slice();const out=b.slice();if(out.length===4)out.push(['5','Alt+5',...Array.from({length:26},(_,i)=>'Alt+'+String.fromCharCode(65+i))].find(x=>!out.includes(x)));return out;}
 function setBinding(current,index,value){if(!Number.isInteger(index)||index<0||index>4||!valid(value||'')||current.some((x,i)=>i!==index&&x===value))return null;const next=bindings({shortcuts:current});next[index]=value;return next;}
 function floatKnown(f,now){const count=f?.basis==='outstanding-upper-bound'?f.upperBoundShares:f?.floatShares;return !!(f&&Number.isFinite(count)&&count>0&&typeof f.source==='string'&&Number.isFinite(Date.parse(f.date))&&Date.parse(f.date)<=now&&now-Date.parse(f.date)<45*86400000);}
 function floatBelow(f,cap,now){return floatKnown(f,now)&&(f.basis==='outstanding-upper-bound'?f.upperBoundShares:f.floatShares)<cap;}
 function floatLabel(f){return f?.basis==='outstanding-upper-bound'?'≤ '+(f.upperBoundShares/1e6).toFixed(2)+'M · IBKR outstanding-share bound':(f.floatShares/1e6).toFixed(2)+'M · '+f.source;}
 function candidate(row,q,evidence,ready,cfg,now){const why=[],e=evidence||{},finite=Number.isFinite;q=q?{...q,last:Object.hasOwn(q,'tradeLast')?q.tradeLast:q.last}:q;
  if(!ready||q?.status!=='LIVE')why.push('fresh LIVE quote missing');
  if(!evidence||!finite(e.at)||now-e.at>90000||e.at>now+5000)why.push('verification missing/stale');
  if(Number(e.conid)!==Number(row.conid))why.push('contract identity unverified');
  if(!e.sessionKnown)why.push('session unknown');if(e.stockType!=='COMMON')why.push('common-stock classification unverified');
  if(!(q?.last>=1.5))why.push('price below $1.50 or unknown');
  const gap=finite(e.previousClose)&&e.previousClose>0&&finite(q?.last)?100*(q.last/e.previousClose-1):null;
  if(gap===null||gap<5)why.push('5% gap not verified');
  if(!finite(e.premarketVolume)||e.premarketVolume<cfg.minVolume)why.push('premarket volume below minimum or unknown');
  const spread=finite(q?.ask)&&finite(q?.bid)&&q.bid>0&&q.ask>=q.bid?q.ask-q.bid:null;
  if(spread===null||spread>Math.min(cfg.maxSpread,.10)+1e-8)why.push('spread too wide or unknown');
  const f=e.float,known=floatKnown(f,now);
  if(cfg.floatMode==='strict'&&!floatBelow(f,cfg.maxFloat||100000000,now))why.push('dated free float below cap required');
  const score=why.length?null:Math.round(Math.min(40,10*Math.log10(Math.max(1,e.premarketVolume/30000)))+Math.min(30,gap)+30*Math.max(0,1-spread/cfg.maxSpread)+(known&&f.floatShares<100000000?10:0));
  return {row,gap,spread,price:q?.last,volume:e.premarketVolume,float:known?f:null,why,score,eligible:why.length===0};
 }
 function rank(rows,quotes,evidence,ready,cfg,now){return rows.map(r=>candidate(r,quotes[r.conid],evidence.get(r.conid),ready(r.conid),cfg,now)).sort((a,b)=>Number(b.eligible)-Number(a.eligible)||(b.volume??-1)-(a.volume??-1)||(a.row.rank??999)-(b.row.rank??999));}
 function shortlist(rows,quotes,evidence,ready,cfg,now){return rank(rows,quotes,evidence,ready,cfg,now).filter(c=>c.eligible).slice(0,4).map(c=>({...c.row,screen:{at:now,gap:c.gap,volume:c.volume,score:c.score,float:c.float}}));}
 // Only successful publication consumes the scheduled scan.
 function scanDue(state,sessions,now){if(state?.retryAt>now)return null;if(state?.retryAt)return 'recovery';if(!state?.publishedAt)return 'initial';const s=(sessions||[]).find(s=>now>=s.start-1800000&&now<s.start);return s&&state.scheduledOpen!==s.start&&!(state.publishedAt>=s.start-1800000&&state.publishedAt<=now)?'scheduled':null;}
 function stableSlots(previous,next){const out=Array(next.length).fill(null),used=new Set();previous.forEach((r,i)=>{const match=next.find(x=>x.conid===r.conid);if(i<out.length&&match){out[i]=match;used.add(match.conid);}});const remaining=next.filter(r=>!used.has(r.conid));return out.map(r=>r||remaining.shift());}
 function screenSnapshot(c,at){return {...c.row,screen:{at,gap:c.gap,volume:c.volume,spread:c.spread,float:c.float,price:c.price,source:'IBKR scanner + verified market data'}};}
 function storyKey(n){return n.provider+':'+(n.articleId||n.url||n.headline);}
 function sameStory(a,b){const clean=s=>String(s||'').toLowerCase().replace(/^\{[^}]*\}/,'').replace(/[^a-z0-9.%]+/g,' ').trim(),x=clean(a.headline),y=clean(b.headline);if(!x||!y)return false;const ta=Date.parse(a.time),tb=Date.parse(b.time);if(Number.isFinite(ta)&&Number.isFinite(tb)&&Math.abs(ta-tb)>86400000)return false;return x===y;}

 return {defaults,chord,bindings,setBinding,candidate,rank,shortlist,scanDue,floatKnown,floatBelow,floatLabel,stableSlots,screenSnapshot,storyKey,sameStory};
});
