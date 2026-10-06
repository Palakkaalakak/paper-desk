const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync(__dirname+'/paper_local.html','utf8');
const match=html.match(/openStock:(async function\(symbol,qty,limit,isCurrent\)\{[\s\S]*?\n  \}),\n  active:/);
assert.ok(match,'Actual adapter implementation must be present');
function harness(response){
 const context={S:{bookId:'b1'},tab:'vmi',sel:null,TICKET:{},vmiHandoffSequence:0,Number,Error,AbortSignal,encodeURIComponent,
   usingTws:()=>true,bookOf:()=>({name:'Paper desk'}),setTimeout:()=>{},setStatus:()=>{},
   fetch:async()=>({ok:true,json:async()=>response}),submit:()=>{throw Error('Handoff must not submit');},applyFill:()=>{throw Error('Handoff must not fill');}};
 context.pick=inst=>{context.sel=inst;context.picks=(context.picks||0)+1;};
 vm.createContext(context);context.open=vm.runInContext('('+match[1]+')',context);return context;
}
const stock={symbol:'META',conid:123,secType:'STK',currency:'USD',served_by:'tws'};
test('Stock magnet resolves contract and only prefills a limit ticket',async()=>{const h=harness(stock);await h.open('META',7,450,()=>true);assert.equal(h.sel.conid,123);assert.equal(h.tab,'trade');assert.equal(h.TICKET.qty,'7');assert.equal(h.TICKET.limit,'450');assert.equal(h.TICKET.type,'LMT');assert.equal(h.picks,1);});
test('Invalid quantity or price cannot navigate',async()=>{for(const [q,p] of [[0,1],[1.5,1],[1,0],[1,Infinity]]){const h=harness(stock);await assert.rejects(h.open('META',q,p,()=>true));assert.equal(h.picks,undefined);}});
test('Mismatched contracts and stale selection cannot navigate',async()=>{for(const result of [{...stock,symbol:'MSFT'},{...stock,currency:'EUR'},{...stock,secType:'OPT'},{...stock,conid:0}]){const h=harness(result);await assert.rejects(h.open('META',1,100,()=>true));assert.equal(h.picks,undefined);}const h=harness(stock);await assert.rejects(h.open('META',1,100,()=>false));assert.equal(h.tab,'vmi');});
test('Book switch during qualification cancels handoff',async()=>{const h=harness(stock);h.fetch=async()=>{h.S.bookId='b2';return {ok:true,json:async()=>stock};};await assert.rejects(h.open('META',1,100,()=>true),/changed/);assert.equal(h.picks,undefined);});
test('A newer handoff supersedes an older request',async()=>{const h=harness(stock);const waits=[];h.fetch=()=>new Promise(resolve=>waits.push(resolve));const first=h.open('META',1,100,()=>true);const second=h.open('META',2,99,()=>true);waits[0]({ok:true,json:async()=>stock});await assert.rejects(first,/changed/);waits[1]({ok:true,json:async()=>stock});await second;assert.equal(h.TICKET.qty,'2');assert.equal(h.picks,1);});
