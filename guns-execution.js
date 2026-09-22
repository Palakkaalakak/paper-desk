/* Browser-local paper execution only. No broker order APIs. */
(function(root,f){if(typeof module==='object'&&module.exports)module.exports=f(require('./guns.js'));else root.GunsExecution=f(root.Guns);})(globalThis,function(C){
'use strict';
return function(a){
  const state=()=>a.state(),depthBooks=new Map();
  const l2Applies=setup=>[4,5].includes(Number(setup))&&cfg().l2Enabled;
  function g(){const s=state();s.guns=s.guns||{config:{},books:{}};s.guns.books=s.guns.books||{};return s.guns;}
  function cfg(){return Object.assign({},C.defaults,g().config);}
  function book(){const all=g().books,id=state().bookId;return all[id]||(all[id]={notes:{},active:[],journal:[]});}
  const pending=()=>state().orders.filter(o=>o.status==='working'&&o.guns?.role==='entry');
  const exposure=()=>book().active.length>0||pending().length>0;
  const fresh=()=>state().positions.every(p=>!p.qty||a.ready(p.conid));
  function sizing(p,inst,price=p.limit){const acc=a.account();return C.size(acc.netLiq,Number(cfg().riskPct),price,p.stop,Math.max(0,acc.bp),n=>a.commission(inst,n,price,'BUY')+a.commission(inst,n,p.stop,'SELL'));}
  function issues(p,inst){const out=[...(p?.errors||['No valid plan'])];if(!inst?.conid||inst.secType!=='STK')out.push('Select a verified stock');if(!a.usingTws()||!a.ready(inst?.conid))out.push('Fresh LIVE Gateway quote required');if(!fresh())out.push('Every held position must have a fresh mark');if(!sizing(p||{},inst).qty)out.push('Risk / buying power permits no whole shares');if(book().active.length+pending().length>=2)out.push('Two pending/open GUNS trades maximum');if(inst&&(a.position(inst.conid)?.qty||state().orders.some(o=>o.status==='working'&&(o.conid===inst.conid||o.legs?.some(l=>l.conid===inst.conid)))))out.push('Symbol already has a position or working order');return out;}
  function orderIssues(p,inst){
    const out=[...(p?.calculationErrors||[])],now=a.now();
    if(!inst?.conid||inst.secType!=='STK')out.push('Select a stock');
    if(p?.chartConid!=null&&(Number(p.chartConid)!==Number(inst?.conid)||p.chartSymbol!==inst?.symbol))out.push('Chart contract does not match selected stock');
    if(![p?.entry,p?.limit,p?.stop,p?.target,p?.tick].every(Number.isFinite)||!(p.stop>0&&p.entry>p.stop&&p.limit>=p.entry&&p.target>p.entry&&p.tick>0))out.push('Strategy price levels are not calculable yet');
    if(!Number.isFinite(p?.session?.start)||!Number.isFinite(p?.session?.end)||p.session.end<=p.session.start||C.day(p.session.start)!==C.day(now)||now>=p.session.end)out.push('Current regular-market session required');
    const qty=sizing(p||{},inst).qty;if(!Number.isSafeInteger(qty)||qty<=0)out.push('Risk / buying power permits no whole shares');
    if(inst&&(a.position(inst.conid)?.qty||state().orders.some(o=>o.status==='working'&&(o.conid===inst.conid||o.legs?.some(l=>l.conid===inst.conid)))))out.push('Symbol already has a position or working order');
    return [...new Set(out)];
  }
  function cancel(o,note){o.status='cancelled';o.note=note;a.save();return true;}
  function arm(p,inst,notes,confirmedPlan=false){const errors=confirmedPlan?orderIssues(p,inst):issues(p,inst);if(errors.length)return {errors};const r=sizing(p,inst),s=state();const o={...inst,id:a.uid(),ts:a.now(),day:a.today(),side:'BUY',qty:r.qty,filledQty:0,type:'STPLMT',stop:p.entry,limit:p.limit,tif:'DAY',status:'working',commission:0,guns:{role:'entry',version:C.VERSION,confirmedPlan,setup:p.setup,plan:JSON.parse(JSON.stringify(confirmedPlan?{...p,expiresAt:p.setup<=3?p.session.end:p.expiresAt}:p)),notes:JSON.parse(JSON.stringify(confirmedPlan?{...notes,warnings:issues(p,inst)}:notes||{})),bookId:s.bookId,equityAtArm:r.equity,budgetAtArm:r.budget}};s.orders.push(o);a.save();a.sync();return {order:o,errors:[]};}
  function manualTrade(inst,spec,plan={},notes={}){
    const side=spec.side,price=Number(spec.price),qty=Number(spec.qty),errors=[];
    if(!inst?.conid||inst.secType!=='STK')errors.push('Select a stock');
    if(!['BUY','SELL'].includes(side)||!Number.isFinite(price)||price<=0||!Number.isSafeInteger(qty)||qty<=0||!Number.isFinite(price*qty))errors.push('Enter a positive finite price and whole-share quantity');
    if(side==='SELL'&&qty>(a.position(inst?.conid)?.qty||0))errors.push('Sell quantity exceeds held long shares');
    const supplied=x=>x!==''&&x!=null,protectedEntry=side==='BUY'&&(supplied(spec.stop)||supplied(spec.target)),stop=Number(spec.stop),target=Number(spec.target);
    if(protectedEntry&&(!supplied(spec.stop)||!supplied(spec.target)||!Number.isFinite(stop)||!Number.isFinite(target)||stop<=0||stop>=price||target<=price))errors.push('Optional long bracket requires stop below fill and target above fill; or leave both blank');
    if(errors.length)return {errors};
    const now=a.now(),warnings=issues(plan,inst),manualPlan={...plan,entry:price,limit:price,stop,target,tick:plan.tick||.01,setup:Number(spec.setup)||1,session:{start:now,end:plan.session?.end>now?plan.session.end:null}};
    const o={...inst,mult:1,id:a.uid(),ts:now,day:a.today(),side,qty,filledQty:0,type:'MANUAL',tif:'DAY',status:'working',commission:0,note:'USER-ENTERED PAPER FILL — strategy/data warnings overridden',priceSource:'USER_ENTERED_PAPER',guns:{role:protectedEntry?'entry':'manual',userOverride:true,bookId:state().bookId,setup:manualPlan.setup,plan:manualPlan,notes:JSON.parse(JSON.stringify({...notes,userOverride:true,warnings})),equityAtFill:a.account().netLiq,budgetAtFill:protectedEntry?qty*(price-stop):null,rewardR:protectedEntry?(target-price)/(price-stop):null,breakeven:!!cfg().breakeven}};
    state().orders.push(o);const result=a.fill(o,qty,price);if(result===false){o.status='rejected';a.save();return {order:o,errors:[o.note||'Invalid accounting inputs']};}a.save();a.sync();return {order:o,errors:[]};
  }
  function depthItems(){const out=new Map();for(const o of pending())if(l2Applies(o.guns.setup))out.set(o.conid,o);for(const b of book().active)if(l2Applies(b.setup))out.set(b.inst.conid,b.inst);return [...out.values()];}
  function depthGuard(target,position=false){const meta=position?target:target.guns,inst=position?target.inst:target;if(!l2Applies(meta.setup))return true;
    const d=depthBooks.get(state().bookId+':'+inst.conid),result=C.depthRisk(d,inst,position?{entry:target.entry,stop:target.originalStop}:meta.plan,cfg(),a.now()),s=meta.l2||(meta.l2={mode:'waiting'}),before=JSON.stringify(s);
    s.valid=result.valid;s.reason=result.reason;s.flags=result.flags;s.metrics=result.metrics||null;
    if(!result.valid){s.lastKey='';s.hits=0;if(s.mode!=='review')s.mode='waiting';}
    else {const sample=String(result.stream)+':'+result.revision;
      if(sample!==s.sample){if(result.key&&result.key===s.lastKey&&result.stream===s.stream){s.hits=(s.hits||0)+1;}else{s.hits=result.key?1:0;s.firstAt=result.at;}s.sample=sample;s.lastKey=result.key;s.stream=result.stream;}
      const confirmed=result.key&&s.hits>=2&&result.at-s.firstAt>=1000;
      const override=s.overrideKey===result.key&&s.overrideStream===result.stream&&s.overrideUntil>a.now();
      if(confirmed&&!override){s.mode='review';s.finding=result.flags.map(f=>f.text).join('; ');if(!position&&cfg().l2AutoCancel){s.mode='cancelled';cancel(target,'Level II: '+s.finding);}}
      else if(s.mode!=='review')s.mode=result.key&&!override?'checking':'clear';
      if(position&&s.mode==='review'&&s.loggedKey!==result.key){s.loggedKey=result.key;target.events.push({at:a.now(),type:'LEVEL II WARNING',detail:s.finding});}
    }
    if(before!==JSON.stringify(s))a.save();
    return result.valid&&s.mode==='clear';
  }
  function updateDepth(inst,data,bid=state().bookId){if(bid!==state().bookId)return;depthBooks.set(bid+':'+inst.conid,data);if(depthBooks.size>12)depthBooks.delete(depthBooks.keys().next().value);pending().filter(o=>o.conid===inst.conid).forEach(o=>depthGuard(o));book().active.filter(b=>b.inst.conid===inst.conid).forEach(b=>depthGuard(b,true));}
  function depthDecision(id,action){const o=pending().find(o=>o.id===id),b=book().active.find(b=>b.id===id),target=o||b;if(!target)return false;const meta=o?o.guns:b,s=meta.l2;if(!s)return false;
    if(action==='cancel'&&o)return cancel(o,'User cancelled after Level II review');
    if(!['resume','ack'].includes(action))return false;
    const inst=o||b.inst,d=depthBooks.get(state().bookId+':'+inst.conid),result=C.depthRisk(d,inst,o?o.guns.plan:{entry:b.entry,stop:b.originalStop},cfg(),a.now());
    if(!result.valid)return false;
    s.mode='clear';s.overrideKey=result.key;s.overrideStream=result.stream;s.overrideUntil=a.now()+5000;s.decisionAt=a.now();
    if(b)b.events.push({at:a.now(),type:'LEVEL II ACKNOWLEDGED',detail:s.finding});a.save();return true;
  }
  function guard(o){if(o.guns)return true;const owned=new Set([...book().active.map(b=>b.inst.conid),...pending().map(p=>p.conid)]);const legs=o.legs||[o];for(const l of legs){if(!owned.has(l.conid))continue;const held=a.position(l.conid)?.qty||0,remaining=(o.qty-o.filledQty)*(l.ratio||1);if(o.legs||l.side!=='SELL'||remaining>held||held<=0){o.status='rejected';o.note='GUNS owns this symbol; cancel entry / flatten bracket first';a.save();return false;}}return true;}
  function fill(o){if(!o.guns)return null;if(o.guns.role!=='entry'||o.status!=='working')return false;const p=o.guns.plan,now=a.now();if(o.guns.bookId!==state().bookId)return cancel(o,'Portfolio changed');if(now>=p.expiresAt)return cancel(o,'Paper entry expired');if(now<p.session.start)return false;if(!depthGuard(o))return false;if(!a.usingTws()||!a.ready(o.conid)||!fresh())return false;if(a.position(o.conid)?.qty)return cancel(o,'Symbol exposure changed');const q=a.quotes()[o.conid],r=sizing(p,o);const changed=o.qty!==r.qty||o.guns.budgetAtFill!==r.budget;o.qty=r.qty;o.guns.equityAtFill=r.equity;o.guns.budgetAtFill=r.budget;if(!r.qty)return cancel(o,'Current equity / buying power permits no whole shares');if(changed)a.save();
    // Revalidate the setup with current bars before any trigger. A changed pattern
    // requires explicit re-arming instead of silently moving an approved entry.
    if(!o.guns.confirmedPlan){
      const current=a.validate?a.validate(o):null;
      if(q.ask>o.limit && Number.isFinite(q.tradeLast??q.last) && (q.tradeLast??q.last)>=o.stop)return cancel(o,'No chase: ask exceeded stop-limit cap');
      if(!current||current.errors?.length)return false;
      if(['entry','limit','stop'].some(k=>Math.abs(current[k]-p[k])>1e-7))return cancel(o,'Setup changed; review and re-arm');
      if(q.ask-q.bid>Number(cfg().maxSpread)+1e-9)return false;
    }
    // Confirmed prices are immutable; a changing chart is not a new order.
    if(!Number.isFinite(q.bid)||!Number.isFinite(q.ask)||q.bid<=0||q.ask<q.bid)return false;
    const last=Object.hasOwn(q,'tradeLast')?q.tradeLast:q.last;
    if(!o.guns.confirmedPlan||!o.triggered){if(!Number.isFinite(last)||last<o.stop)return false;if(o.guns.confirmedPlan){o.triggered=true;a.save();}}
    if(q.ask>o.limit)return o.guns.confirmedPlan?false:cancel(o,'No chase: ask exceeded stop-limit cap');
    // Full risk-sized paper simulation: displayed size is evidence, not a silent order-size cap.
    const full=!!o.guns.confirmedPlan;
    if(!(q.askSize>0))return false;
    const price=full?q.ask:a.marketPrice(o,'BUY',Math.min(r.qty,Math.floor(q.askSize)));
    if(!Number.isFinite(price)||price>o.limit||price<=p.stop||(full&&price>=p.target))return false;
    const filledSize=full?sizing(p,o,price):r,count=full?filledSize.qty:Math.min(r.qty,Math.floor(q.askSize));if(!Number.isSafeInteger(count)||count<=0)return false;
    o.guns.plannedQty=filledSize.qty;o.guns.cancelledQty=full?0:r.qty-count;o.qty=count;o.guns.rewardR=Number(cfg().rewardR);o.guns.breakeven=!!cfg().breakeven;o.triggered=true;
    o.guns.fillModel=full?'RISK_SIZED_PAPER':'DISPLAYED_SIZE_PAPER';o.guns.riskPctAtFill=Number(cfg().riskPct);o.guns.budgetAtFill=filledSize.budget;o.guns.fundedRisk=count*(price-p.stop);o.guns.displayedAskSize=q.askSize;
    a.fill(o,count,price);a.save();return true;
  }
  function after(o,n,price,oldQty,newQty,realized,fee){const b=book(),now=a.now();if(o.guns?.role==='entry'&&o.side==='BUY'){
    const p=o.guns.plan,initialR=price-p.stop;
    b.active.push({id:o.id,inst:{conid:o.conid,symbol:o.symbol,secType:'STK',mult:1,exch:'SMART',brokerId:true},entry:price,qty:n,originalQty:n,stop:p.stop,originalStop:p.stop,target:o.guns.userOverride||o.guns.confirmedPlan?p.target:C.round(price+initialR*o.guns.rewardR,p.tick,true),initialR,breakeven:o.guns.breakeven,sessionEnd:p.session.end,openedAt:now,setup:p.setup,version:C.VERSION,plan:p,notes:o.guns.notes,equityAtFill:o.guns.equityAtFill,budgetAtFill:o.guns.budgetAtFill,fillModel:o.guns.fillModel||'DISPLAYED_SIZE_PAPER',riskPctAtFill:o.guns.riskPctAtFill,fundedRisk:initialR*n,fees:fee,maxBid:price,minBid:price,userOverride:!!o.guns.userOverride,priceSource:o.priceSource||'IB_OBSERVED',events:[{at:now,type:'ENTRY',price,qty:n,priceSource:o.priceSource||'IB_OBSERVED'}]});
  }else if(o.side==='SELL'){
    const matches=b.active.filter(x=>x.inst.conid===o.conid).sort((x,y)=>Number(y.id===o.guns?.parentId)-Number(x.id===o.guns?.parentId));let remaining=n;
    for(const x of matches){const sold=Math.min(x.qty,remaining);if(!sold)continue;remaining-=sold;x.qty-=sold;x.fees+=fee*sold/n;x.events.push({at:now,type:o.guns?.reason||'MANUAL EXIT',price,qty:sold,priceSource:o.priceSource||'IB_OBSERVED'});
      if(x.qty===0){x.closedAt=now;x.net=x.events.filter(e=>e.type!=='ENTRY'&&e.qty).reduce((v,e)=>v+(e.price-x.entry)*e.qty,0)-x.fees;x.actualR=riskStats(x).budgetR;b.active=b.active.filter(y=>y!==x);b.journal.unshift(x);}}
    if(newQty===0)state().orders.forEach(other=>{if(other.status==='working'&&other.conid===o.conid&&other.side==='SELL')cancel(other,'Position flat; sibling exit cancelled');});
  }}
  function manage(cid){let changed=false;for(const b of [...book().active]){if(cid!=null&&b.inst.conid!==cid)continue;if(!a.usingTws()||!a.ready(b.inst.conid))continue;const q=a.quotes()[b.inst.conid];if(!(q.bid>0))continue;if(q.bid>b.maxBid||q.bid<b.minBid)changed=true;b.maxBid=Math.max(b.maxBid,q.bid);b.minBid=Math.min(b.minBid,q.bid);const decision=C.exit(b,q,a.now());if(decision.stop!==b.stop){b.stop=decision.stop;b.events.push({at:a.now(),type:'BREAKEVEN',price:b.stop});changed=true;}
    if(!decision.reason)continue;if(decision.reason==='STOP'&&!b.stopTriggered){b.stopTriggered=true;changed=true;b.events.push({at:a.now(),type:'STOP TRIGGER',price:q.bid});}
    const held=a.position(b.inst.conid)?.qty||0,n=b.fillModel==='RISK_SIZED_PAPER'?(q.bidSize>0?Math.min(b.qty,held):0):Math.min(b.qty,held,Math.floor(q.bidSize||0)),tick=q.receivedAt||q.at;
    if(n<=0||b.lastExitTick===tick)continue;
    const price=b.fillModel==='RISK_SIZED_PAPER'?(decision.reason==='TARGET'?b.target:q.bid):a.marketPrice(b.inst,'SELL',n);if(!Number.isFinite(price)||price<=0||(decision.reason==='TARGET'&&price<b.target))continue;
    b.lastExitTick=tick;const o={...b.inst,id:a.uid(),ts:a.now(),day:a.today(),side:'SELL',qty:n,filledQty:0,type:'MKT',tif:'DAY',status:'working',commission:0,guns:{role:'exit',parentId:b.id,reason:decision.reason}};
    state().orders.push(o);a.fill(o,n,price);changed=true;
  }if(changed)a.save();return changed;}
  function riskStats(b){
    const perShare=Number.isFinite(b.entry)&&Number.isFinite(b.originalStop)?b.entry-b.originalStop:b.initialR;
    const funded=perShare*b.originalQty,budget=Number.isFinite(b.correction?.riskBudget)?b.correction.riskBudget:Number.isFinite(b.budgetAtFill)&&b.budgetAtFill>0?b.budgetAtFill:null;
    return {perShare,funded,budget,budgetR:budget?b.net/budget:null,positionR:funded>0?b.net/funded:null,stopBudgetR:budget?funded/budget:null};
  }
  function correctionPreview(id,spec){
    const b=book().journal.find(x=>x.id===id),errors=[];
    if(!b||b.qty!==0||!Number.isFinite(b.net))return {errors:['Select a closed trade with a valid recorded result']};
    if(spec.bookId!==state().bookId)errors.push('Portfolio changed; reopen the correction');
    if(Number(spec.revision)!==(b.corrections?.length||0))errors.push('Journal changed; reopen the correction');
    const price=Number(spec.exitPrice),budget=Number(spec.riskBudget),risk=riskStats(b).perShare,mode=spec.mode;
    if(!['budget','shares'].includes(mode)||!Number.isFinite(price)||price<=b.entry||!Number.isFinite(risk)||risk<=0)errors.push('TP must be above entry and original stop must be below entry');
    if(!Number.isFinite(budget)||budget<=0)errors.push('Enter the original dollar risk budget (1R)');
    if(!String(spec.reason||'').trim())errors.push('A correction reason is required');
    const targetR=(price-b.entry)/risk,net=Math.round((mode==='budget'?budget*targetR:(price-b.entry)*b.originalQty-b.fees)*100)/100,delta=net-b.net;
    if(![targetR,net,delta,state().cash,state().realized,state().cash+delta,state().realized+delta].every(Number.isFinite))errors.push('Invalid correction accounting');
    return {errors,b,price,riskBudget:budget,targetR,net,delta,mode};
  }
  function correctJournal(id,spec){
    const p=correctionPreview(id,spec);if(p.errors.length)return p;
    const b=p.b,s=state(),now=a.now(),before=b.net;
    const item={id:a.uid(),at:now,bookId:s.bookId,journalId:id,source:'USER_JOURNAL_CORRECTION',outcome:'TARGET',mode:p.mode,exitPrice:p.price,riskBudget:p.riskBudget,targetR:p.targetR,previousNet:before,net:p.net,delta:p.delta,reason:String(spec.reason).trim()};
    if(!b.originalRecord)b.originalRecord=JSON.parse(JSON.stringify(b));
    b.corrections=b.corrections||[];b.corrections.push(item);b.correction=item;b.net=p.net;b.actualR=p.net/p.riskBudget;
    s.cash=Math.round((s.cash+p.delta)*1e6)/1e6;s.realized+=p.delta;
    s.trades=s.trades||[];s.trades.unshift({id:item.id,ts:now,orderId:id,symbol:b.inst.symbol,conid:b.inst.conid,secType:'STK',side:'ADJUST',qty:0,price:p.price,commission:0,realized:p.delta,cashAfter:s.cash,mult:1,priceSource:item.source,combo:'Journal TP correction ('+p.mode+')',correction:item});
    s.log=s.log||[];s.log.unshift({ts:now,text:b.inst.symbol+' manual journal TP correction: '+before.toFixed(2)+' → '+p.net.toFixed(2)+'; account delta '+p.delta.toFixed(2)});
    a.snapshot?.();a.save();return {errors:[],correction:item};
  }
  function migrateRisk(){
    const data=g();if(data.riskAccountingVersion===2)return;
    data.config.breakeven=false;
    for(const b of Object.values(data.books)){
      for(const x of b.active||[]){x.breakeven=false;
        if(x.stop===x.entry&&x.originalStop<x.entry&&(x.events||[]).some(e=>e.type==='BREAKEVEN')){
          const trigger=(x.events||[]).filter(e=>e.type==='STOP TRIGGER').at(-1);
          x.stop=x.originalStop;if(x.stopTriggered&&trigger?.price>x.originalStop)x.stopTriggered=false;
          x.events.push({at:a.now(),type:'BREAKEVEN DISABLED',price:x.stop,detail:'Restored original SL by requested migration'});
        }
      }
      for(const x of b.journal||[]){if(x.legacyPositionR===undefined)x.legacyPositionR=x.actualR;x.actualR=riskStats(x).budgetR;}
    }
    data.riskAccountingVersion=2;a.save();
  }
  migrateRisk();
  function pulse(){book().active.forEach(b=>depthGuard(b,true));manage();pending().forEach(fill);}
  function flatten(id){const b=book().active.find(x=>x.id===id);if(b){b.forceExit=true;a.save();manage(b.inst.conid);}}
  return {state,cfg,g,book,pending,fresh,sizing,riskStats,correctionPreview,correctJournal,issues,orderIssues,arm,manualTrade,guard,fill,after,manage,pulse,flatten,exposure,depthItems,updateDepth,depthGuard,depthDecision};
};
});
