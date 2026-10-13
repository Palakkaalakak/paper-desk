'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {create,encodeExport,decodeExport,cachedDecision,CACHE_VERSION,CACHE_WEEK,CALCULATION_REVISION}=require('./vmi-scanner.js');

test('audit export deduplicates without dropping any evidence or proposal',()=>{
 const observation={end:'2025-12-31',val:123,tag:'Revenue',source:'https://issuer.example/'+ 'source-path/'.repeat(100),filed:'2026-02-01',unit:'USD'};
 const evidence={series:{revenue:[observation]},current:{revenue:structuredClone(observation)}};
 const research={scannerEvidence:structuredClone(evidence),flows:evidence.series,balances:evidence.current,valuationDecision:{method:'FCF',missing:['growth']}};
 const content={kind:'paper-desk-vmi-sp500',version:1,rows:Array.from({length:20},(_,i)=>({symbol:'X'+i,result:{checks:[{status:'ACQUIRE',observations:[observation]}]},evidence,research,candidate:{automatic:structuredClone(research)}})),diagnostics:{requests:[]}};
 const packed=encodeExport(content),restored=decodeExport(JSON.parse(JSON.stringify(packed)));
 assert.deepEqual(restored,content);assert.equal(packed.version,2);
 assert.ok(JSON.stringify(packed).length<JSON.stringify(content).length/4);
 assert.strictEqual(decodeExport(content),content);
});

test('export codec preserves literal ref keys and rejects invalid reference graphs',()=>{
 const content={kind:'paper-desk-vmi-sp500',version:1,rows:[JSON.parse('{"$ref":2,"__proto__":{"safe":true}}')]};
 assert.deepEqual(decodeExport(encodeExport(content)),content);assert.equal({}.safe,undefined);
 const invalid={kind:'paper-desk-vmi-sp500',version:2,encoding:'json-dag-v1',root:{$ref:0},nodes:[{loop:{$ref:0}}]};
 assert.throws(()=>decodeExport(invalid),/Cyclic/);
 invalid.root={$ref:99};assert.throws(()=>decodeExport(invalid),/Invalid/);
});
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

test('cheap gates precede statements; missing broad fields go directly to financial acquisition',async()=>{
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
  assert.equal(u.pathname,'/data/vmi_sp500_company');
  assert.ok(calls.some(c=>c.pathname.endsWith('batch')&&c.searchParams.get('view')==='growth'&&c.searchParams.get('symbols').includes('X502')));
  const symbol=u.searchParams.get('symbol');assert.ok(!['X0','X1','X2'].includes(symbol));
  return response({member:{symbol},result:{preset:'moderate',status:symbol==='X3'?'REJECTED':'QUALIFIED',failures:symbol==='X3'?['Sourced statement gate']:[],pending:[]},evidence:{},research:null});
 };
 try{const scanner=create({mode:()=> 'moderate'});await scanner.start();const s=scanner.state();assert.equal(s.stats.rejected,4);assert.equal(s.stats.qualified,499);assert.deepEqual(s.diagnostics.stages.map(s=>s.excluded),[1,1,1,1]);assert.equal(s.diagnostics.stages[1].done,502);assert.equal(s.diagnostics.stages[3].done,500);assert.equal(s.diagnostics.active.length,0);}
 finally{global.fetch=original;}
});

function memoryCache(){
 const records=new Map();return {records,load:async()=>new Map(structuredClone([...records])),put:async r=>records.set(r.key,structuredClone(r)),clear:async()=>records.clear()};
}
const presets=['conservative','moderate','optimistic'];
function fixtureData(symbol,mode,status='QUALIFIED'){
 const evaluations=Object.fromEntries(presets.map(p=>[p,{preset:p,status,failures:status==='REJECTED'?['Fixture criterion']:[],pending:status==='ACQUIRING'?['history']:[]}]));
 return {cacheVersion:2,calculationRevision:CALCULATION_REVISION,member:{symbol,cik:1},result:evaluations[mode],evaluations,evidence:{series:{revenue:[{end:'2025-12-31',val:100,source:'fixture'}]}},research:null,collectedAt:Date.now(),acquiredModes:[mode]};
}
function cacheFixtureFetch(calls,{excludeConservative=false,unresolved=false}={}){
 return async (path,options)=>{
  const u=new URL(path,'http://localhost');calls.push(u);
  if(u.pathname.endsWith('members'))return response({rows:universe.map((r,i)=>({...r,cik:1,industryExcluded:i>1})),retrievedAt:Date.now()});
  if(u.pathname.endsWith('batch'))return response({rows:u.searchParams.get('symbols').split(',').map(symbol=>{
   const screens=Object.fromEntries(presets.map(p=>[p,{rejected:excludeConservative&&symbol==='X0'&&p==='conservative',reasons:['ROE fixture']}])) ;
   return {symbol,fields:{ROE:'13'},source:'fixture',retrievedAt:Date.now(),screens,screen:screens[u.searchParams.get('mode')]};
  }),missing:[]});
  if(u.pathname==='/data/vmi_sp500_supplement'){
   assert.equal(options.method,'POST');assert.equal(options.headers['X-Paper-Desk-Research'],'1');
   const body=JSON.parse(options.body);assert.ok(body.prior.evidence.series.revenue.length);
   return response({...fixtureData(body.symbol,body.mode,unresolved?'ACQUIRING':'QUALIFIED'),collectedAt:body.prior.collectedAt,evidence:body.prior.evidence});
  }
  assert.equal(u.pathname,'/data/vmi_sp500_company');return response(fixtureData(u.searchParams.get('symbol'),u.searchParams.get('mode'),unresolved?'ACQUIRING':'QUALIFIED'));
 };
}
test('persistent cache restores without downloads; preset and profile reuse collection dates',async()=>{
 const original=global.fetch,calls=[],cache=memoryCache();let mode='moderate';global.fetch=cacheFixtureFetch(calls);
 try{
  const scanner=create({mode:()=>mode,cache});await scanner.start();assert.equal(scanner.state().stats.qualified,2);
  const collected=cache.records.get('X0').collectedAt,first=calls.length;
  const reloaded=create({mode:()=>mode,cache});await reloaded.restore();assert.equal(calls.length,first);assert.equal(reloaded.state().stats.qualified,2);
  mode='optimistic';await reloaded.reapply();assert.equal(calls.length,first);assert.equal(reloaded.state().preset,mode);
  await reloaded.reapply();assert.equal(calls.length,first);assert.equal(cache.records.get('X0').collectedAt,collected);
  assert.match(reloaded.state().cacheStatus,/collected/);
 }finally{global.fetch=original;}
});
test('previously broad-excluded company acquires additional history only after looser rules',async()=>{
 const original=global.fetch,calls=[],cache=memoryCache();let mode='conservative';global.fetch=cacheFixtureFetch(calls,{excludeConservative:true});
 try{
  const s=create({mode:()=>mode,cache});await s.start();assert.ok(!cache.records.has('X0'));assert.equal(s.state().stats.qualified,1);
  const before=calls.length;mode='moderate';await s.reapply();const more=calls.slice(before);
  assert.equal(more.filter(u=>u.pathname.endsWith('company')).length,1);assert.equal(more.find(u=>u.pathname.endsWith('company')).searchParams.get('symbol'),'X0');
  assert.equal(more.filter(u=>u.pathname.endsWith('batch')&&u.searchParams.get('view')==='financial').length,0);
  assert.ok(cache.records.has('X0'));assert.equal(s.state().stats.qualified,2);
 }finally{global.fetch=original;}
});
test('newly required unresolved inputs request supplementation once per preset',async()=>{
 const original=global.fetch,calls=[],cache=memoryCache();let mode='moderate';global.fetch=cacheFixtureFetch(calls,{unresolved:true});
 try{
  const s=create({mode:()=>mode,cache});await s.start();const before=calls.length;mode='conservative';await s.reapply();
  const more=calls.slice(before);assert.equal(more.length,2);assert.ok(more.every(u=>u.pathname==='/data/vmi_sp500_supplement'&&!u.searchParams.has('force')));
  const after=calls.length;await s.reapply();assert.equal(calls.length,after);assert.equal(s.state().stats.acquiring,2);
 }finally{global.fetch=original;}
});
test('failed or expiry-extending supplementation leaves saved evidence intact',async()=>{
 const original=global.fetch,calls=[],cache=memoryCache();let mode='moderate';const load=cacheFixtureFetch(calls,{unresolved:true});global.fetch=load;
 try{
  const s=create({mode:()=>mode,cache});await s.start();const before=structuredClone([...cache.records]);
  global.fetch=async(path,options)=>path.startsWith('/data/vmi_sp500_supplement')?{ok:false,status:502}:load(path,options);
  mode='conservative';await s.reapply();assert.deepEqual([...cache.records],before);
  global.fetch=async(path,options)=>{const r=await load(path,options);if(path.startsWith('/data/vmi_sp500_supplement')){const d=await r.json();d.collectedAt+=1000;return response(d);}return r;};
  await s.reapply();assert.deepEqual([...cache.records],before);
 }finally{global.fetch=original;}
});
test('rescan bypasses every browser stage and deletion leaves no reusable evidence',async()=>{
 const original=global.fetch,calls=[],cache=memoryCache();global.fetch=cacheFixtureFetch(calls);
 try{
  const s=create({mode:()=> 'moderate',cache});await s.start();const before=calls.length;await s.start(true);
  assert.ok(calls.slice(before).length>=5);assert.ok(calls.slice(before).every(u=>u.searchParams.get('force')==='1'));
  await s.clearCache();assert.equal(cache.records.size,0);assert.equal(s.state().rows.length,0);
  const after=calls.length;await s.reapply();assert.equal(calls.length,after);assert.equal(s.state().rows.length,0);
 }finally{global.fetch=original;}
});
test('collection expiry, future dates, schema and issuer mismatch cannot reuse evidence',()=>{
 const data=fixtureData('X0','moderate'),record={version:CACHE_VERSION,collectedAt:Date.now(),data},member={symbol:'X0',cik:1};
 assert.ok(cachedDecision(record,'moderate',member));
 assert.equal(cachedDecision({...record,collectedAt:Date.now()-CACHE_WEEK},'moderate',member),null);
 assert.equal(cachedDecision({...record,collectedAt:Date.now()+60000},'moderate',member),null);
 assert.equal(cachedDecision({...record,version:0},'moderate',member),null);
 assert.equal(cachedDecision(record,'moderate',{...member,cik:2}),null);
 data.evaluations.moderate.pending=['missing'];assert.equal(cachedDecision(record,'moderate',member),null);
});
test('old accounting verdicts migrate evidence without renewing expiry or reusing stale passes',async()=>{
 const original=global.fetch,calls=[],cache=memoryCache();global.fetch=cacheFixtureFetch(calls);
 try{
  const s=create({mode:()=> 'moderate',cache});await s.start();
  const stamp=Date.now()-60000;
  for(const key of ['X0','X1']){const r=cache.records.get(key);delete r.data.calculationRevision;r.collectedAt=stamp;}
  const before=calls.length,reloaded=create({mode:()=> 'moderate',cache});await reloaded.restore();
  assert.equal(reloaded.state().stats.qualified,0);assert.equal(calls.length,before);
  assert.equal(reloaded.state().stats.acquiring,2);
  await reloaded.start();
  assert.equal(calls.length,before+2);assert.ok(calls.slice(before).every(u=>u.pathname==='/data/vmi_sp500_supplement'));
  assert.equal(cache.records.get('X0').collectedAt,stamp);
  assert.equal(cache.records.get('X0').data.calculationRevision,CALCULATION_REVISION);
  assert.equal(reloaded.state().stats.qualified,2);
 }finally{global.fetch=original;}
});

test('legacy verdict migration failure preserves evidence but never displays old qualification',async()=>{
 const original=global.fetch,calls=[],cache=memoryCache();global.fetch=cacheFixtureFetch(calls);
 try{
  const s=create({mode:()=> 'moderate',cache});await s.start();
  for(const key of ['X0','X1'])delete cache.records.get(key).data.calculationRevision;
  const before=structuredClone([...cache.records]);
  global.fetch=async()=>({ok:false,status:502});
  await s.start();assert.equal(s.state().stats.qualified,0);assert.deepEqual([...cache.records],before);
 }finally{global.fetch=original;}
});

test('storage failure remains visible and never claims persistence',async()=>{
 const original=global.fetch;global.fetch=cacheFixtureFetch([]);
 const cache={load:async()=>new Map(),put:async()=>{throw Error('Quota exceeded');},clear:async()=>{throw Error('Denied');}};
 try{const s=create({mode:()=> 'moderate',cache});await s.start();assert.equal(s.state().stats.qualified,2);assert.match(s.state().cacheStatus,/Quota exceeded/);await s.clearCache();assert.match(s.state().cacheStatus,/Denied/);}
 finally{global.fetch=original;}
});
test('delete waits for in-flight persistence and cancelled responses cannot refill cache',async()=>{
 const original=global.fetch,cache=memoryCache();let release,entered;const pending=new Promise(r=>entered=r);const put=cache.put;
 cache.put=async r=>{if(r.key==='X0'){entered();await new Promise(resolve=>release=resolve);}await put(r);};global.fetch=cacheFixtureFetch([]);
 try{
  const s=create({mode:()=> 'moderate',cache}),scanning=s.start();await pending;const deleting=s.clearCache();release();await Promise.all([scanning,deleting]);
  assert.equal(cache.records.size,0);assert.equal(s.state().rows.length,0);
 }finally{global.fetch=original;}
});
