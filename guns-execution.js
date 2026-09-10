/* Browser-local paper execution only. No broker order APIs. */
(function(root,f){if(typeof module==='object'&&module.exports)module.exports=f(require('./guns.js'));else root.GunsExecution=f(root.Guns);})(globalThis,function(C){
'use strict';
return function(a){
  const state=()=>a.state();
  function g(){const s=state();s.guns=s.guns||{config:{},books:{}};s.guns.books=s.guns.books||{};return s.guns;}
  function cfg(){return Object.assign({},C.defaults,g().config);}
  function book(){const all=g().books,id=state().bookId;return all[id]||(all[id]={notes:{},active:[],journal:[]});}
  const pending=()=>state().orders.filter(o=>o.status==='working'&&o.guns?.role==='entry');
  const exposure=()=>book().active.length>0||pending().length>0;
  const fresh=()=>state().positions.every(p=>!p.qty||a.ready(p.conid));
  function sizing(p,inst){const acc=a.account();return C.size(acc.netLiq,Number(cfg().riskPct),p.limit,p.stop,Math.max(0,acc.bp),n=>a.commission(inst,n,p.limit,'BUY')+a.commission(inst,n,p.stop,'SELL'));}
  function issues(p,inst){const out=[...(p?.errors||['No valid plan'])];if(!inst?.conid||inst.secType!=='STK')out.push('Select a verified stock');if(!a.usingTws()||!a.ready(inst?.conid))out.push('Fresh LIVE Gateway quote required');if(!fresh())out.push('Every held position must have a fresh mark');if(!sizing(p||{},inst).qty)out.push('Risk / buying power permits no whole shares');if(book().active.length+pending().length>=2)out.push('Two pending/open GUNS trades maximum');if(inst&&(a.position(inst.conid)?.qty||state().orders.some(o=>o.status==='working'&&(o.conid===inst.conid||o.legs?.some(l=>l.conid===inst.conid)))))out.push('Symbol already has a position or working order');return out;}
  function cancel(o,note){o.status='cancelled';o.note=note;a.save();return true;}
  function arm(p,inst,notes){const errors=issues(p,inst);if(errors.length)return {errors};const r=sizing(p,inst),s=state();const o={...inst,id:a.uid(),ts:a.now(),day:a.today(),side:'BUY',qty:r.qty,filledQty:0,type:'STPLMT',stop:p.entry,limit:p.limit,tif:'DAY',status:'working',commission:0,guns:{role:'entry',version:C.VERSION,setup:p.setup,plan:JSON.parse(JSON.stringify(p)),notes:{...notes},bookId:s.bookId,equityAtArm:r.equity,budgetAtArm:r.budget}};s.orders.push(o);a.save();a.sync();return {order:o,errors:[]};}
  function guard(o){if(o.guns)return true;const owned=new Set([...book().active.map(b=>b.inst.conid),...pending().map(p=>p.conid)]);const legs=o.legs||[o];for(const l of legs){if(!owned.has(l.conid))continue;const held=a.position(l.conid)?.qty||0,remaining=(o.qty-o.filledQty)*(l.ratio||1);if(o.legs||l.side!=='SELL'||remaining>held||held<=0){o.status='rejected';o.note='GUNS owns this symbol; cancel entry / flatten bracket first';a.save();return false;}}return true;}
  function fill(o){if(!o.guns)return null;if(o.guns.role!=='entry'||o.status!=='working')return false;const p=o.guns.plan,now=a.now();if(o.guns.bookId!==state().bookId)return cancel(o,'Portfolio changed');if(now>=p.expiresAt)return cancel(o,'Setup entry window expired');if(!a.usingTws()||!a.ready(o.conid)||!fresh())return false;if(a.position(o.conid)?.qty)return cancel(o,'Symbol exposure changed');const q=a.quotes()[o.conid],r=sizing(p,o);const changed=o.qty!==r.qty||o.guns.budgetAtFill!==r.budget;o.qty=r.qty;o.guns.equityAtFill=r.equity;o.guns.budgetAtFill=r.budget;if(!r.qty)return cancel(o,'Current equity / buying power permits no whole shares');if(changed)a.save();
    // Revalidate the setup with current bars before any trigger. A changed pattern
    // requires explicit re-arming instead of silently moving an approved entry.
    const current=a.validate?a.validate(o):null;
    if(q.ask>o.limit && Number.isFinite(q.tradeLast??q.last) && (q.tradeLast??q.last)>=o.stop)return cancel(o,'No chase: ask exceeded stop-limit cap');
    if(!current||current.errors?.length)return false;
    if(['entry','limit','stop'].some(k=>Math.abs(current[k]-p[k])>1e-7))return cancel(o,'Setup changed; review and re-arm');
    if(now<p.session.start||q.ask-q.bid>Number(cfg().maxSpread)+1e-9)return false;
    const last=Object.hasOwn(q,'tradeLast')?q.tradeLast:q.last;
    if(!Number.isFinite(last)||last<o.stop)return false;
    if(q.ask>o.limit)return cancel(o,'No chase: ask exceeded stop-limit cap');
    const count=Math.min(r.qty,Math.floor(q.askSize||0));if(count<=0)return false;
    const price=a.marketPrice(o,'BUY',count);if(!Number.isFinite(price)||price>o.limit||price<=p.stop)return false;
    o.guns.plannedQty=r.qty;o.guns.cancelledQty=r.qty-count;o.qty=count;o.guns.rewardR=Number(cfg().rewardR);o.guns.breakeven=!!cfg().breakeven;o.triggered=true;
    a.fill(o,count,price);a.save();return true;
  }
  function after(o,n,price,oldQty,newQty,realized,fee){const b=book(),now=a.now();if(o.guns?.role==='entry'&&o.side==='BUY'){
    const p=o.guns.plan,initialR=price-p.stop;
    b.active.push({id:o.id,inst:{conid:o.conid,symbol:o.symbol,secType:'STK',mult:1,exch:'SMART',brokerId:true},entry:price,qty:n,originalQty:n,stop:p.stop,originalStop:p.stop,target:C.round(price+initialR*o.guns.rewardR,p.tick,true),initialR,breakeven:o.guns.breakeven,sessionEnd:p.session.end,openedAt:now,setup:p.setup,version:C.VERSION,plan:p,notes:o.guns.notes,equityAtFill:o.guns.equityAtFill,budgetAtFill:o.guns.budgetAtFill,fees:fee,maxBid:price,minBid:price,events:[{at:now,type:'ENTRY',price,qty:n}]});
  }else if(o.side==='SELL'){
    const x=b.active.find(x=>x.inst.conid===o.conid);if(!x)return;
    x.qty=Math.max(0,Math.min(x.qty-n,newQty));x.fees+=fee;x.events.push({at:now,type:o.guns?.reason||'MANUAL EXIT',price,qty:n});
    if(x.qty===0){x.closedAt=now;x.net=x.events.filter(e=>e.type!=='ENTRY'&&e.qty).reduce((v,e)=>v+(e.price-x.entry)*e.qty,0)-x.fees;x.actualR=x.net/(x.initialR*x.originalQty);b.active=b.active.filter(y=>y!==x);b.journal.unshift(x);state().orders.forEach(other=>{if(other.status==='working'&&other.conid===o.conid&&other.side==='SELL')cancel(other,'Position flat; sibling exit cancelled');});}
  }}
  function manage(cid){let changed=false;for(const b of [...book().active]){if(cid!=null&&b.inst.conid!==cid)continue;if(!a.usingTws()||!a.ready(b.inst.conid))continue;const q=a.quotes()[b.inst.conid];if(!(q.bid>0))continue;if(q.bid>b.maxBid||q.bid<b.minBid)changed=true;b.maxBid=Math.max(b.maxBid,q.bid);b.minBid=Math.min(b.minBid,q.bid);const decision=C.exit(b,q,a.now());if(decision.stop!==b.stop){b.stop=decision.stop;b.events.push({at:a.now(),type:'BREAKEVEN',price:b.stop});changed=true;}
    if(!decision.reason)continue;if(decision.reason==='STOP'&&!b.stopTriggered){b.stopTriggered=true;changed=true;b.events.push({at:a.now(),type:'STOP TRIGGER',price:q.bid});}
    const held=a.position(b.inst.conid)?.qty||0,n=Math.min(b.qty,held,Math.floor(q.bidSize||0)),tick=q.receivedAt||q.at;
    if(n<=0||b.lastExitTick===tick)continue;
    const price=a.marketPrice(b.inst,'SELL',n);if(!Number.isFinite(price)||price<=0||(decision.reason==='TARGET'&&price<b.target))continue;
    b.lastExitTick=tick;const o={...b.inst,id:a.uid(),ts:a.now(),day:a.today(),side:'SELL',qty:n,filledQty:0,type:'MKT',tif:'DAY',status:'working',commission:0,guns:{role:'exit',parentId:b.id,reason:decision.reason}};
    state().orders.push(o);a.fill(o,n,price);changed=true;
  }if(changed)a.save();return changed;}
  function pulse(){manage();pending().forEach(fill);}
  function flatten(id){const b=book().active.find(x=>x.id===id);if(b){b.forceExit=true;a.save();manage(b.inst.conid);}}
  return {state,cfg,g,book,pending,fresh,sizing,issues,arm,guard,fill,after,manage,pulse,flatten,exposure};
};
});
