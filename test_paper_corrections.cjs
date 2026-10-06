const test=require('node:test'),assert=require('node:assert/strict'),C=require('./paper-corrections.js');
const inst={conid:123,symbol:'META',secType:'STK',currency:'USD',mult:1};
const now=Date.parse('2026-10-06T12:00:00Z');
const row=(id,day,side,qty,price,realized)=>({...inst,id,ts:Date.parse('2026-10-0'+day+'T12:00:00Z'),side,qty,price,commission:1,realized,cashAfter:8000});
function book(){return {account:{type:'cash',mode:'Custom'},cash:9998,realized:198,positions:[{...inst,qty:5,avgCost:100}],orders:[],trades:[row('b',1,'BUY',10,100,-1),row('s',4,'SELL',5,140,199)],cashflows:[],equity:[{ts:now,equity:10500}],log:[]};}
function input(){return {symbol:'META',side:'BUY',qty:10,price:120,fees:2,executedAt:'2026-10-02T08:00:00-04:00',requestId:'test-request-1',reason:'Missed paper execution'};}
test('strict calendar, offset and past timestamp validation',()=>{
 assert.equal(C.timestamp(input().executedAt,now),Date.parse('2026-10-02T12:00:00Z'));
 for(const t of ['2026-10-02T12:00','2026-02-30T12:00:00Z','2026-10-02T25:00Z','2026-10-02T12:00+14:01','2027-01-01T12:00Z'])assert.throws(()=>C.timestamp(t,now));
});
test('chronological replay previews later realized changes without mutation',()=>{
 const b=book(),before=JSON.stringify(b),p=C.preview(b,inst,input(),{},now);
 assert.equal(JSON.stringify(b),before);assert.equal(p.positionAfter.qty,15);assert.equal(p.positionAfter.avgCost,110);
 assert.equal(p.cashDelta,-1202);assert.equal(p.realizedDelta,-52);assert.equal(p.laterTradeChanges[0].after,149);
 const n=C.apply(b,p,{},now+1000);assert.equal(n.cash,8796);assert.equal(n.realized,146);assert.equal(n.trades.find(t=>t.id==='s').cashAfter,6798);
 assert.equal(n.paperCorrections[0].originalTrades[0].realized,199);assert.deepEqual(n.equity,b.equity);
 assert.equal(n.trades.find(t=>t.id.startsWith('retro-')).recordedAt,'2026-10-06T12:00:01.000Z');
 assert.equal(n.trades.find(t=>t.id.startsWith('retro-')).executedAt,'2026-10-02T12:00:00.000Z');
 assert.throws(()=>C.preview(n,inst,input(),{},now),/Duplicate/);
});
test('blocks incomplete, short, managed, working and derivative histories',()=>{
 let b=book();b.positions[0].qty=4;assert.throws(()=>C.preview(b,inst,input(),{},now),/reconcile/);
 assert.throws(()=>C.preview(book(),inst,{...input(),side:'SELL',qty:20},{},now),/exceeds/);
 assert.throws(()=>C.preview(book(),inst,input(),{protectedConids:[123]},now),/managed/);
 b=book();b.orders=[{status:'working',legs:[{conid:123}]}];assert.throws(()=>C.preview(b,inst,input(),{},now),/working/);
 b=book();b.positions.push({conid:55,underConid:123,secType:'OPT',qty:-1});assert.throws(()=>C.preview(b,inst,input(),{},now),/derivatives/);
 b=book();b.trades[1].strategyType='GUNS';assert.throws(()=>C.preview(b,inst,input(),{},now),/managed P&L/);
});
test('stale preview, duplicates and cash overdraw are rejected',()=>{
 let b=book(),p=C.preview(b,inst,input(),{},now);b.cash++;assert.throws(()=>C.apply(b,p,{},now),/changed/);
 assert.throws(()=>C.preview(book(),inst,{...input(),executedAt:'2026-10-01T12:00:00Z'},{},now),/ties/);
 b=book();b.cash=10;assert.throws(()=>C.preview(b,inst,input(),{},now),/overdraw/);
});
test('storage failures leave account untouched and remove new backup',()=>{
 const b=book(),s={...b,bookId:'b1',books:[{id:'b1',data:b}]},before=JSON.stringify(s),m=new Map([['paperAccount','old']]);
 const storage={getItem:k=>m.get(k)??null,setItem:(k,v)=>{if(k==='paperAccount')throw Error('quota');m.set(k,v);},removeItem:k=>m.delete(k)};
 const n=C.apply(b,C.preview(b,inst,input(),{},now),{},now);
 assert.throws(()=>C.persist(storage,s,n,Object.keys(b).concat('paperCorrections'),'test-request-1'),/quota/);
 assert.equal(JSON.stringify(s),before);assert.equal(m.size,1);assert.equal(m.get('paperAccount'),'old');
 storage.setItem=(k,v)=>m.set(k,v);const saved=C.persist(storage,s,n,Object.keys(n),'test-request-1');
 assert.equal(saved.next.books[0].data.cash,n.cash);assert.equal(JSON.stringify(s),before);assert.equal(JSON.parse(m.get('paperAccount')).paperCorrections.length,1);
 assert.throws(()=>C.persist(storage,s,n,Object.keys(n),'test-request-1'),/backup already/);
});
