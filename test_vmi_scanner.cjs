'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {create}=require('./vmi-scanner.js');
const universe=Array.from({length:503},(_,i)=>({symbol:'X'+i,name:'Issuer '+i,industry:'Software'}));
const response=data=>({ok:true,json:async()=>data});

test('explicit scan traverses all 503 listings without broker endpoints or first-100 limit',async()=>{
 const original=global.fetch,calls=[];
 global.fetch=async path=>{
  calls.push(path);const u=new URL(path,'http://localhost');
  if(u.pathname==='/data/vmi_sp500_members')return response({rows:universe});
  if(u.pathname==='/data/vmi_sp500_batch')return response({rows:u.searchParams.get('symbols').split(',').map(symbol=>({symbol,fields:{},source:'fixture',screen:{rejected:symbol==='X0',reasons:symbol==='X0'?['Fixture failure']:[]}})),missing:[]});
  assert.equal(u.pathname,'/data/vmi_sp500_company');
  return response({member:{symbol:u.searchParams.get('symbol')},result:{preset:u.searchParams.get('mode'),status:'QUALIFIED',failures:[],pending:[]},evidence:{},research:null});
 };
 try{
  const scanner=create({mode:()=> 'moderate',discount:()=>10});
  assert.equal(calls.length,0);await scanner.start();
  assert.equal(scanner.state().phase,'Scan complete');
  assert.equal(scanner.state().stats.total,503);assert.equal(scanner.state().stats.qualified,502);
  assert.equal(calls.filter(p=>p.startsWith('/data/vmi_sp500_company')).length,502);
  assert.equal(scanner.state().rows.find(r=>r.symbol==='X502').status,'QUALIFIED');
 }finally{global.fetch=original;}
});

test('incomplete universe never becomes a complete scan',async()=>{
 const original=global.fetch;global.fetch=async()=>response({rows:universe.slice(0,100)});
 try{const scanner=create({mode:()=> 'moderate'});await scanner.start();assert.equal(scanner.state().phase,'Scan stopped');assert.equal(scanner.state().stats.total,0);}
 finally{global.fetch=original;}
});

test('late response cannot contaminate a restarted preset',async()=>{
 const original=global.fetch;let resolveOld,mode='moderate',count=0;
 global.fetch=async path=>{
  if(++count===1)return new Promise(resolve=>{resolveOld=resolve;});
  if(path.includes('members'))return response({rows:universe});
  const u=new URL(path,'http://localhost');
  return response({rows:u.searchParams.get('symbols').split(',').map(symbol=>({symbol,fields:{},source:'fixture',screen:{rejected:true,reasons:['Rejected in current run']}})),missing:[]});
 };
 try{
  const scanner=create({mode:()=>mode});const old=scanner.start();scanner.cancel();mode='optimistic';
  await scanner.start();resolveOld(response({rows:universe}));await old;
  assert.equal(scanner.state().preset,'optimistic');assert.equal(scanner.state().phase,'Scan complete');
  assert.equal(scanner.state().stats.rejected,503);
 }finally{global.fetch=original;}
});

test('browser workers are bounded and inconsistent passes stay unproven',async()=>{
 const original=global.fetch;let batch=0,deep=0,peakBatch=0,peakDeep=0;
 global.fetch=async path=>{
  const u=new URL(path,'http://localhost');
  if(u.pathname.endsWith('members'))return response({rows:universe});
  const isBatch=u.pathname.endsWith('batch');
  if(isBatch){batch++;peakBatch=Math.max(peakBatch,batch);}else{deep++;peakDeep=Math.max(peakDeep,deep);}
  await new Promise(r=>setTimeout(r,2));
  if(isBatch){batch--;return response({rows:u.searchParams.get('symbols').split(',').map(symbol=>({symbol,fields:{},source:'fixture',screen:{rejected:false,reasons:[]}})),missing:[]});}
  deep--;const symbol=u.searchParams.get('symbol');
  return response({member:{symbol},result:{preset:'moderate',status:'QUALIFIED',failures:[],pending:symbol==='X0'?['unproven']:[]},evidence:{},research:null});
 };
 try{const scanner=create({mode:()=> 'moderate'});await scanner.start();assert.equal(peakBatch,3);assert.equal(peakDeep,4);assert.equal(scanner.state().stats.qualified,502);assert.equal(scanner.state().rows.find(r=>r.symbol==='X0').status,'ACQUIRING');}
 finally{global.fetch=original;}
});

test('all cheap gates finish before recovery; exclusions never receive deep work',async()=>{
 const original=global.fetch,calls=[];
 global.fetch=async path=>{
  const u=new URL(path,'http://localhost');calls.push(u);
  if(u.pathname.endsWith('members'))return response({rows:universe.map((r,i)=>({...r,industryExcluded:i===0}))});
  if(u.pathname.endsWith('batch')){
   assert.equal(u.searchParams.get('recovery'),'deferred');
   const symbols=u.searchParams.get('symbols').split(',');assert.ok(!symbols.includes('X0'));
   const growth=u.searchParams.get('view')==='growth';
   if(growth)assert.ok(!symbols.includes('X1'));
   return response({rows:symbols.filter(s=>s!=='X3').map(symbol=>({symbol,fields:{},source:'fixture',screen:{rejected:symbol==='X1'||growth&&symbol==='X2',reasons:['fixture gate']}})),missing:symbols.includes('X3')?['X3']:[]});
  }
  if(u.pathname.endsWith('recover')){
   assert.equal(u.searchParams.get('symbol'),'X3');
   assert.ok(calls.some(c=>c.pathname.endsWith('batch')&&c.searchParams.get('view')==='growth'&&c.searchParams.get('symbols').includes('X502')));
   return response({rows:[{symbol:'X3',fields:{},source:'fixture',screen:{rejected:true,reasons:['recovery gate']}}],missing:[]});
  }
  assert.equal(u.pathname,'/data/vmi_sp500_company');
  const symbol=u.searchParams.get('symbol');assert.ok(!['X0','X1','X2','X3'].includes(symbol));
  return response({member:{symbol},result:{preset:'moderate',status:'QUALIFIED',failures:[],pending:[]},evidence:{},research:null});
 };
 try{const scanner=create({mode:()=> 'moderate'});await scanner.start();const s=scanner.state();assert.equal(s.stats.rejected,4);assert.equal(s.stats.qualified,499);assert.deepEqual(s.diagnostics.stages.map(s=>s.excluded),[1,1,1,1,0]);assert.equal(s.diagnostics.stages[1].done,502);assert.equal(s.diagnostics.stages[3].done,1);assert.equal(s.diagnostics.active.length,0);}
 finally{global.fetch=original;}
});
