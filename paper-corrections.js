/* Local historical stock accounting. Never calls a broker or the live fill engine. */
(function(root){'use strict';
const copy=x=>JSON.parse(JSON.stringify(x)),finite=Number.isFinite;
const need=(ok,msg)=>{if(!ok)throw Error(msg);};
const round=x=>Math.round(x*1e6)/1e6, cents=x=>Math.round(x*100)/100;
function timestamp(value,now=Date.now()){
 const m=String(value).match(/^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?(Z|[+-]\d{2}:\d{2})$/);
 need(m,'Execution time requires an explicit timezone, e.g. 2026-10-01T14:30:00-04:00');
 const day=Date.parse(m[1]+'T00:00:00Z'),offset=m[5];
 need(finite(day)&&new Date(day).toISOString().slice(0,10)===m[1]&&+m[2]<24&&+m[3]<60&&+(m[4]||0)<60,'Invalid execution date/time');
 need(offset==='Z'||(+offset.slice(1,3)<=14&&+offset.slice(4)<60&&(+offset.slice(1,3)!==14||+offset.slice(4)===0)),'Invalid timezone offset');
 const t=Date.parse(value);need(finite(t)&&t>0&&t<=now,'Execution must be in the past');return t;
}
function replay(rows){
 let qty=0,avgCost=0,realized=0;const results=new Map();
 for(const t of rows.slice().sort((a,b)=>a.ts-b.ts)){
  need(t.secType==='STK'&&(t.mult==null||t.mult===1)&&['BUY','SELL'].includes(t.side)&&Number.isSafeInteger(t.qty)&&t.qty>0&&finite(t.price)&&t.price>0&&finite(t.commission)&&t.commission>=0&&finite(t.ts),'Unsupported or incomplete retained stock history');
  let pnl=-t.commission;
  if(t.side==='BUY'){avgCost=(avgCost*qty+t.price*t.qty)/(qty+t.qty);qty+=t.qty;}
  else{need(t.qty<=qty,'Historical sale exceeds shares owned then; short/incomplete histories cannot be replayed');pnl+=(t.price-avgCost)*t.qty;qty-=t.qty;if(!qty)avgCost=0;}
  realized+=pnl;results.set(t.id,{realized:cents(pnl),qty,avgCost});
 }
 return {qty,avgCost,realized,results};
}
function fingerprint(book,context){
 // Exclude live marks/log noise, but include every accounting and managed-state input.
 return JSON.stringify([book.account,book.cash,book.realized,book.positions,book.orders,book.trades,book.cashflows,book.paperCorrections||[],context]);
}
function preview(book,inst,input,context={},now=Date.now()){
 need(inst&&Number.isSafeInteger(inst.conid)&&inst.conid>0&&inst.secType==='STK'&&inst.currency==='USD'&&(inst.mult==null||inst.mult===1),'A resolved ordinary USD stock is required');
 need(['BUY','SELL'].includes(input.side)&&Number.isSafeInteger(input.qty)&&input.qty>0&&finite(input.price)&&input.price>0&&finite(input.fees)&&input.fees>=0,'Positive whole shares/price and nonnegative fees required');
 need(/^[A-Za-z0-9_-]{8,100}$/.test(input.requestId||''),'Stable correction request ID required');
 need(typeof input.reason==='string'&&input.reason.trim().length>=3,'Explain why this paper execution is being recorded');
 need(finite(book.cash)&&finite(book.realized),'Invalid account balances');
 const ts=timestamp(input.executedAt,now),id='retro-'+input.requestId;
 need(!(book.paperCorrections||[]).some(a=>a.requestId===input.requestId)&&!(book.trades||[]).some(t=>t.id===id),'Duplicate correction request');
 need(!(context.protectedConids||[]).includes(inst.conid),'Active managed position: reconcile its strategy journal before historical recording');
 const related=x=>x.conid===inst.conid||x.underConid===inst.conid||x.root===inst.symbol||x.symbol===inst.symbol||String(x.symbol||'').startsWith(inst.symbol+' ');
 need(!(book.orders||[]).some(o=>['working','pending','submitted'].includes(o.status)&&(related(o)||(o.legs||[]).some(related))),'Cancel affected working orders before recording');
 need(!(book.positions||[]).some(p=>p.qty&&p.secType!=='STK'&&related(p)),'Related derivatives exist; historical coverage requires separate reconciliation');
 const rows=(book.trades||[]).filter(t=>t.conid===inst.conid),current=(book.positions||[]).filter(p=>p.conid===inst.conid);
 need(current.length<=1,'Duplicate current position records');
 need(new Set(rows.map(t=>t.id)).size===rows.length&&rows.every(t=>t.id),'Missing or duplicate retained trade IDs');
 need(!rows.some(t=>t.ts===ts),'Execution timestamp ties an existing fill; specify a distinct execution second');
 need(!rows.some(t=>Math.abs(t.ts-ts)<1000&&t.side===input.side&&t.qty===input.qty&&t.price===input.price&&t.commission===input.fees),'Possible duplicate execution');
 const old=replay(rows),position=current[0];
 need(old.qty===(position?.qty||0)&&Math.abs(old.avgCost-(position?.avgCost||0))<1e-6,'Retained fills do not reconcile current quantity/cost; incomplete history is unsafe');
 for(const t of rows)need(finite(t.realized)&&Math.abs(t.realized-old.results.get(t.id).realized)<=.031,'Retained realized P&L does not reconcile');
 const trade={conid:inst.conid,symbol:inst.symbol,secType:'STK',currency:'USD',exch:inst.exch||'SMART',mult:1,id,orderId:id,ts,executedAt:new Date(ts).toISOString(),executionInput:input.executedAt,recordedAt:new Date(now).toISOString(),side:input.side,qty:input.qty,price:input.price,commission:input.fees,priceSource:'USER_RECORDED_HISTORICAL_PAPER',userOverride:true,portfolioMode:book.account?.mode||'Custom',reason:input.reason.trim()};
 const after=replay([...rows,trade]);trade.realized=after.results.get(id).realized;
 const laterTradeChanges=rows.filter(t=>Math.abs(old.results.get(t.id).realized-after.results.get(t.id).realized)>.00001).map(t=>({tradeId:t.id,executedAt:new Date(t.ts).toISOString(),before:t.realized,after:after.results.get(t.id).realized}));
 need(!laterTradeChanges.some(x=>{const t=rows.find(t=>t.id===x.tradeId);return t.strategyType||t.combo||(context.managedTradeIds||[]).includes(t.id);}),'Later managed P&L would change; strategy-journal reconciliation required');
 const cashDelta=round((input.side==='BUY'?-1:1)*input.qty*input.price-input.fees),realizedDelta=after.realized-old.realized;
 need(book.account?.type!=='cash'||book.cash+cashDelta>=0,'This would overdraw the current cash account');
 return {requestId:input.requestId,input:copy(input),inst:copy(inst),base:fingerprint(book,context),trade,cashBefore:book.cash,cashDelta,cashAfter:round(book.cash+cashDelta),realizedBefore:book.realized,realizedDelta,realizedAfter:book.realized+realizedDelta,positionBefore:position?copy(position):null,positionAfter:{qty:after.qty,avgCost:after.avgCost},laterTradeChanges,warnings:['User-recorded paper execution; no IB order is sent.','Historical equity marks, buying power and strategy journals are not reconstructed.','Later affected cost-basis/P&L changes and cashAfter adjustments are explicitly audited.']};
}
function apply(book,prepared,context={},now=Date.now()){
 need(prepared.base===fingerprint(book,context),'Account changed since preview; preview again');
 const p=preview(book,prepared.inst,prepared.input,context,now),next=copy(book),originalTrades=[];
 for(const t of next.trades){
  const change=p.laterTradeChanges.find(c=>c.tradeId===t.id),cash=t.ts>p.trade.ts&&finite(t.cashAfter);
  if(change||cash){originalTrades.push(copy(t));if(change)t.realized=change.after;if(cash)t.cashAfter=round(t.cashAfter+p.cashDelta);}
 }
 next.cash=p.cashAfter;next.realized=p.realizedAfter;
 next.positions=next.positions.filter(x=>x.conid!==p.inst.conid);
 if(p.positionAfter.qty)next.positions.push({...p.inst,...p.positionBefore,...p.positionAfter,opened:p.positionBefore?.opened||p.trade.ts,openedToday:false});
 next.trades.push(p.trade);next.trades.sort((a,b)=>b.ts-a.ts);
 next.orders.unshift({...p.inst,id:p.trade.orderId,type:'HISTORICAL',status:'filled',side:p.trade.side,qty:p.trade.qty,filledQty:p.trade.qty,avgFill:p.trade.price,commission:p.trade.commission,ts:p.trade.ts,filledAt:p.trade.ts,recordedAt:p.trade.recordedAt,note:p.trade.reason});
 const audit={requestId:p.requestId,recordedAt:p.trade.recordedAt,executedAt:p.trade.executedAt,executionInput:p.input.executedAt,reason:p.trade.reason,trade:copy(p.trade),before:{cash:book.cash,realized:book.realized,position:p.positionBefore},after:{cash:next.cash,realized:next.realized,position:p.positionAfter},laterTradeChanges:p.laterTradeChanges,originalTrades,equityRestated:false};
 (next.paperCorrections||(next.paperCorrections=[])).push(audit);
 (next.log||(next.log=[])).unshift({ts:now,text:'Historical paper trade recorded: '+p.requestId});return next;
}
function persist(storage,state,nextBook,fields,requestId){
 const before=copy(state),next=copy(state),b=next.books.find(b=>b.id===next.bookId);
 need(b,'Target paper book missing');
 for(const k of fields){if(nextBook[k]!==undefined)next[k]=copy(nextBook[k]);}
 b.data=copy(nextBook);
 const backupKey='paperAccount.backup.missed-trade.'+requestId;
 need(storage.getItem(backupKey)===null,'Correction backup already exists; inspect audit before retrying');
 // No in-memory mutation until BOTH writes succeed. A failed main write removes only our new backup.
 storage.setItem(backupKey,JSON.stringify(before));
 try{storage.setItem('paperAccount',JSON.stringify(next));}catch(e){try{storage.removeItem(backupKey);}catch(ignore){}throw e;}
 return {next,backupKey};
}
root.PaperCorrections={timestamp,replay,preview,apply,fingerprint,persist};
if(typeof module!=='undefined')module.exports=root.PaperCorrections;
})(globalThis);
