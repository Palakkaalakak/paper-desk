const test=require('node:test'),a=require('node:assert/strict'),C=require('./vmi-calls.js');
const stock={conid:123,symbol:'META',secType:'STK',qty:500};
const inst={conid:456,symbol:'META  990116C00100000',secType:'OPT',right:'C',root:'META',underConid:123,currency:'USD',mult:100,expiry:'20990116',tradingClass:'META',deliverableStatus:'IB_STANDARD_CLASS_NOT_OCC_VERIFIED'};
const state=()=>({positions:[stock],orders:[]});
const observation=()=>({served_by:'tws',inst:{...inst},quote:{brokerConid:456,bid:2,ask:2.1,status:'LIVE',at:Date.now()}});
test('coverage subtracts short calls, pending stock sales and partial combo call legs',()=>{
 const s=state();s.positions.push({...inst,qty:-1,deliverableConfirmed:true});
 s.orders.push({id:'stock',conid:123,symbol:'META',secType:'STK',side:'SELL',qty:50,filledQty:10,status:'working'});
 s.orders.push({id:'combo',qty:2,filledQty:1,status:'working',legs:[{...inst,side:'SELL',ratio:2,deliverableConfirmed:true}]});
 a.deepEqual(C.coverage(s,stock),{owned:500,reserved:340,available:160,contracts:1});
 a.equal(C.coverage(s,stock,'combo').contracts,3);
 s.orders[1].legs[0].deliverableConfirmed=false;a.throws(()=>C.coverage(s,stock),/unverified/);
});
test('covered-call ticket validates contract, entitlement, freshness and coverage',()=>{
 const s=state(),d=observation(),before=JSON.stringify(s);
 a.equal(C.ticket(s,stock,d,1,2,true).conid,456);a.equal(JSON.stringify(s),before);
 for(const patch of [{underConid:999},{currency:'EUR'},{mult:10},{right:'P'},{tradingClass:'META1'},{expiry:'20200101'}])a.throws(()=>C.ticket(s,stock,{...d,inst:{...inst,...patch}},1,2,true));
 for(const patch of [{status:'DELAYED'},{status:'FROZEN'},{at:Date.now()-61000},{bid:null},{ask:1},{brokerConid:789},{halted:true}])a.throws(()=>C.ticket(s,stock,{...d,quote:{...d.quote,...patch}},1,2,true));
 a.throws(()=>C.ticket(s,stock,d,1,2,false),/Confirm/);
 a.throws(()=>C.ticket(s,stock,d,6,2,true),/Insufficient/);
 a.throws(()=>C.ticket(s,stock,d,1.5,2,true));
});
test('existing ambiguous derivative and missing position fail closed',()=>{
 const s=state();s.positions.push({symbol:'META unknown',secType:'OPT',qty:-1});a.throws(()=>C.coverage(s,stock),/unverified/);
 a.throws(()=>C.coverage(state(),null));
});
