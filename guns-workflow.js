/* User-led screening and keyboard helpers; no chart approval or broker orders. */
(function(r,f){if(typeof module==='object'&&module.exports)module.exports=f();else r.GunsWorkflow=f();})(globalThis,function(){'use strict';
 const defaults=['1','2','3','4','5'];
 const valid=x=>/^(?:[1-5]|Alt\+[A-Z0-9])$/.test(x);
 function chord(e){if(e.ctrlKey||e.metaKey||e.shiftKey||e.repeat||e.isComposing)return null;const code=e.code||'';if(!e.altKey&&/^Digit[1-5]$/.test(code))return code.slice(5);if(e.altKey&&/^(Key[A-Z]|Digit[0-9])$/.test(code))return 'Alt+'+code.replace('Key','').replace('Digit','');return null;}
 function bindings(c){const b=c?.shortcuts;if(!Array.isArray(b)||![4,5].includes(b.length)||new Set(b).size!==b.length||!b.every(valid))return defaults.slice();const out=b.slice();if(out.length===4)out.push(['5','Alt+5',...Array.from({length:26},(_,i)=>'Alt+'+String.fromCharCode(65+i))].find(x=>!out.includes(x)));return out;}
 function setBinding(current,index,value){if(!Number.isInteger(index)||index<0||index>4||!valid(value||'')||current.some((x,i)=>i!==index&&x===value))return null;const next=bindings({shortcuts:current});next[index]=value;return next;}
 function floatKnown(f,now){const count=f?.basis==='outstanding-upper-bound'?f.upperBoundShares:f?.floatShares;return !!(f&&Number.isFinite(count)&&count>0&&typeof f.source==='string'&&f.source.trim().length>0&&Number.isFinite(Date.parse(f.date))&&Date.parse(f.date)<=now&&now-Date.parse(f.date)<45*86400000);}
 function floatBelow(f,cap,now){return floatKnown(f,now)&&(f.basis==='outstanding-upper-bound'?f.upperBoundShares:f.floatShares)<cap;}
 function floatLabel(f){return f?.basis==='outstanding-upper-bound'?'≤ '+(f.upperBoundShares/1e6).toFixed(2)+'M · IBKR outstanding-share bound':(f.floatShares/1e6).toFixed(2)+'M · '+f.source;}
 function quoteIssues(row,q,ready){const why=[],finite=Number.isFinite,last=q&&(Object.hasOwn(q,'tradeLast')?q.tradeLast:q.last);
  if(!ready||q?.status!=='LIVE'||q?.error||q?.halted)why.push('Awaiting fresh LIVE IBKR quote');
  if(q?.brokerConid!=null&&Number(q.brokerConid)!==Number(row.conid))why.push('Awaiting matching IBKR quote contract');
  if(!finite(last)||last<=0)why.push('Awaiting IBKR last trade');
  if(!finite(q?.bid)||q.bid<=0)why.push('Awaiting positive IBKR bid');
  if(!finite(q?.ask)||q.ask<=0)why.push('Awaiting positive IBKR ask');
  if(finite(q?.bid)&&finite(q?.ask)&&q.ask<q.bid)why.push('Awaiting uncrossed IBKR bid/ask');
  return why;
 }
 function candidate(row,q,evidence,ready,cfg,now){const why=quoteIssues(row,q,ready),e=evidence||{},finite=Number.isFinite;let pending=why.length>0;q=q?{...q,last:Object.hasOwn(q,'tradeLast')?q.tradeLast:q.last}:q;
  if(!evidence||!finite(e.at)||now-e.at>90000||e.at>now+5000){pending=true;why.push('Awaiting current IBKR verification');}
  if(Number(e.conid)!==Number(row.conid)){pending=true;why.push('Awaiting matching contract verification');}
  if(!e.sessionKnown){pending=true;why.push('Awaiting IBKR session schedule');}
  if(!e.stockType){pending=true;why.push('Awaiting stock classification');}else if(e.stockType!=='COMMON')why.push('Not a corporate common stock');
  if(finite(q?.last)&&q.last<1.5)why.push('Price below $1.50');
  const gap=finite(e.previousClose)&&e.previousClose>0&&finite(q?.last)?100*(q.last/e.previousClose-1):null;
  if(gap===null){pending=true;why.push('Awaiting prior RTH close and last trade');}else if(gap<5)why.push('Gap below 5%');
  if(!finite(e.premarketVolume)){pending=true;why.push('Awaiting observed premarket volume');}else if(e.premarketVolume<cfg.minVolume)why.push('Premarket volume below minimum');
  const spread=finite(q?.ask)&&finite(q?.bid)&&q.bid>0&&q.ask>=q.bid?q.ask-q.bid:null;
  if(spread===null)pending=true;
  else if(spread>Math.min(cfg.maxSpread,.10)+1e-8)why.push('Spread $'+spread.toFixed(4)+' exceeds $'+Math.min(cfg.maxSpread,.10).toFixed(4));
  const f=e.float,known=floatKnown(f,now);
  if(cfg.floatMode==='strict'&&!known){pending=true;why.push('Awaiting dated float/share-count evidence');}
  else if(cfg.floatMode==='strict'&&!floatBelow(f,cfg.maxFloat||100000000,now))why.push('Float/share-count bound reaches configured cap');
  const score=why.length?null:Math.round(Math.min(40,10*Math.log10(Math.max(1,e.premarketVolume/30000)))+Math.min(30,gap)+30*Math.max(0,1-spread/cfg.maxSpread)+(known&&f.floatShares<100000000?10:0));
  return {row,gap,spread,bid:q?.bid,ask:q?.ask,quoteAt:q?.at,quoteSource:q?.source||'IB Gateway',verificationSource:e.source||'IBKR market data',price:q?.last,previousClose:e.previousClose,sessionDate:e.sessionDate,stockType:e.stockType,volume:e.premarketVolume,float:known?f:null,why,pending,score,eligible:why.length===0};
 }
 function rank(rows,quotes,evidence,ready,cfg,now){return rows.map(r=>candidate(r,quotes[r.conid],evidence.get(r.conid),ready(r.conid),cfg,now)).sort((a,b)=>Number(b.eligible)-Number(a.eligible)||(b.volume??-1)-(a.volume??-1)||(a.row.rank??999)-(b.row.rank??999));}
 function shortlist(rows,quotes,evidence,ready,cfg,now){return rank(rows,quotes,evidence,ready,cfg,now).filter(c=>c.eligible).map(c=>screenSnapshot(c,now)).filter(r=>completeScreen(r,cfg)).slice(0,4);}
 // Only successful publication consumes the scheduled scan.
 function scanDue(state,sessions,now){if(state?.retryAt>now)return null;if(state?.retryAt)return 'recovery';if(!state?.publishedAt)return 'initial';const s=(sessions||[]).find(s=>now>=s.start-1800000&&now<s.start);return s&&state.scheduledOpen!==s.start&&!(state.publishedAt>=s.start-1800000&&state.publishedAt<=now)?'scheduled':null;}
 function stableSlots(previous,next){const out=Array(next.length).fill(null),used=new Set();previous.forEach((r,i)=>{const match=next.find(x=>x.conid===r.conid);if(i<out.length&&match){out[i]=match;used.add(match.conid);}});const remaining=next.filter(r=>!used.has(r.conid));return out.map(r=>r||remaining.shift());}
 function screenSnapshot(c,at){return {...c.row,screen:{at,gap:c.gap,volume:c.volume,spread:c.spread,bid:c.bid,ask:c.ask,quoteAt:c.quoteAt,quoteSource:c.quoteSource,verificationSource:c.verificationSource,previousClose:c.previousClose,sessionDate:c.sessionDate,stockType:c.stockType,float:c.float,price:c.price,source:'IBKR scanner + verified market data'}};}
 // Validate evidence at its screening time: saved cards are snapshots, not live reranks.
 function completeScreen(row,cfg){const s=row?.screen,finite=Number.isFinite;return !!(s&&finite(s.at)&&finite(s.price)&&s.price>=1.5&&finite(s.gap)&&s.gap>=5&&finite(s.volume)&&s.volume>=cfg.minVolume&&finite(s.bid)&&s.bid>0&&finite(s.ask)&&s.ask>=s.bid&&finite(s.spread)&&s.spread>=0&&Math.abs(s.ask-s.bid-s.spread)<1e-8&&s.spread<=Math.min(cfg.maxSpread,.10)+1e-8&&finite(s.quoteAt)&&Math.abs(s.at-s.quoteAt)<15000&&finite(s.previousClose)&&s.previousClose>0&&s.stockType==='COMMON'&&typeof s.sessionDate==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(s.sessionDate)&&floatBelow(s.float,cfg.maxFloat||100000000,s.at));}
 function storyKey(n){return n.provider+':'+(n.articleId||n.url||n.headline);}
 function sameStory(a,b){const clean=s=>String(s||'').toLowerCase().replace(/^\{[^}]*\}/,'').replace(/[^a-z0-9.%]+/g,' ').trim(),x=clean(a.headline),y=clean(b.headline);if(!x||!y)return false;const ta=Date.parse(a.time),tb=Date.parse(b.time);if(Number.isFinite(ta)&&Number.isFinite(tb)&&Math.abs(ta-tb)>86400000)return false;return x===y;}

 return {defaults,chord,bindings,setBinding,candidate,rank,shortlist,scanDue,floatKnown,floatBelow,floatLabel,stableSlots,screenSnapshot,completeScreen,quoteIssues,storyKey,sameStory};
});
