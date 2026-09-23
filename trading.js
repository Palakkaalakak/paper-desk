/* Browser-local Trading/Custom calculations. No broker order APIs. */
(function(root,f){if(typeof module==='object'&&module.exports)module.exports=f(require('./guns.js'));else root.PaperTrading=f(root.Guns);})(globalThis,function(C){
'use strict';
const defaults={riskPct:1,rewardR:2,timeframe:'5',trackMinutes:5,strategy:''};
const supplied=x=>x!==''&&x!=null,positive=x=>Number.isFinite(x)&&x>0;
function atrFor(data,frame,now,period=14){
  if(!data||!Number.isFinite(data.updatedAt)||now-data.updatedAt<0||now-data.updatedAt>90000)throw Error('Fresh broker chart history required for ATR');
  if(!['1','5','15','d'].includes(String(frame)))throw Error('Select a supported chart timeframe');
  const raw=frame==='d'?data.daily:data.minute;
  const dated=b=>frame==='d'?typeof b.t==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(b.t)&&Number.isFinite(Date.parse(b.t)):Number.isFinite(b.t);
  if(!Array.isArray(raw)||!raw.every((b,i)=>dated(b)&&C.validBar(b)&&(!i||b.t>raw[i-1].t)))throw Error('ATR requires ordered, valid broker candles');
  let rows;
  if(frame==='d')rows=raw.filter(b=>b.t<C.day(now));
  else {const n=Number(frame),ms=n*60000;rows=C.aggregate(raw.filter(b=>b.t+60000<=now),n).filter(b=>b.complete&&b.t+ms<=now);
    if(rows.at(-1)?.t!==Math.floor(now/ms)*ms-ms)throw Error('Latest completed '+frame+'m candle unavailable; ATR not invented');
  }
  const value=C.atr(rows,period);if(!positive(value))throw Error('Need at least '+(period+1)+' completed '+frame+' candles for ATR');
  return {value,period,lastAt:rows.at(-1).t,bars:rows.length};
}
function calculate({mode,inst,spec,data,equity,bp,fee,now}){
  if(!['Trading','Custom'].includes(mode))throw Error('Use this preview in a Trading or Custom portfolio');
  if(!inst?.conid||inst.secType!=='STK'||(inst.mult||1)!==1)throw Error('This preview supports long stocks; use the manual ticket for other instruments');
  const entry=Number(spec.entry),riskPct=Number(spec.riskPct),trackMinutes=Number(spec.trackMinutes);
  if(!positive(entry))throw Error('Choose a positive entry price');
  if(!positive(bp))throw Error('Valid positive buying power required');
  if(!Number.isInteger(trackMinutes)||trackMinutes<1||trackMinutes>30)throw Error('Tracking duration must be 1–30 whole minutes');
  if(!['LMT','STPLMT'].includes(spec.type))throw Error('Choose limit or breakout stop-limit entry');
  let stop=supplied(spec.stop)?Number(spec.stop):null,atr=null;
  if(mode==='Trading'&&stop===null){
    if(Number(data?.conid)!==Number(inst.conid)||data?.symbol!==inst.symbol)throw Error('ATR chart does not match selected stock');
    atr=atrFor(data,String(spec.timeframe),now);stop=entry-atr.value;
  }
  if(stop!==null&&(!positive(stop)||stop>=entry))throw Error('SL must be positive and below entry');
  const rewardR=Number(spec.rewardR),tick=positive(data?.minTick)?data.minTick:(entry<1?.0001:.01);
  if(stop!==null)stop=C.round(stop,tick,false);
  if(stop!==null&&!positive(stop))throw Error('Rounded SL must stay above zero');
  let target=supplied(spec.target)?Number(spec.target):null;
  if(mode==='Trading'&&target===null&&!spec.targetLater){if(!positive(rewardR))throw Error('Choose a positive target R');target=C.round(entry+(entry-stop)*rewardR,tick,true);}
  if(target!==null&&(!positive(target)||target<=entry))throw Error('TP must be above entry');
  const budget=mode==='Trading'?equity*riskPct/100:null;
  const qty=mode==='Trading'?C.size(equity,riskPct,entry,stop,bp,n=>fee(n,entry,'BUY')+fee(n,stop,'SELL')).qty:Number(spec.qty);
  if(!Number.isSafeInteger(qty)||qty<=0)throw Error(mode==='Custom'?'Enter a positive whole-share quantity':'Risk / buying power permits no whole shares');
  const cost=qty*entry+fee(qty,entry,'BUY');
  if(!Number.isFinite(cost)||fee(qty,entry,'BUY')<0||cost>bp+1e-8)throw Error('Insufficient buying power');
  return {mode,entry,stop,target,qty,budget,riskPct:mode==='Trading'?riskPct:null,priceR:stop===null?null:entry-stop,
    fundedRisk:stop===null?null:qty*(entry-stop),timeframe:String(spec.timeframe),atr,trackMinutes,type:spec.type,
    strategy:String(spec.strategy||'').trim().slice(0,120),targetLater:target===null,tick,fees:fee(qty,entry,'BUY')+(stop===null?0:fee(qty,stop,'SELL'))};
}
function beginTracking(b,now,minutes){
  const duration=Number.isInteger(minutes)&&minutes>=1&&minutes<=30?minutes:5;
  b.tracking={startedAt:now,endsAt:now+duration*60000,minutes:duration,status:'observing',samples:0,
    min:null,max:null,lastAt:null,lastCheck:now,gapMs:0,gaps:[],gapStart:null,targetHitAt:null};
}
function observe(b,q,ready,now){
  const t=b.tracking;if(!t||t.status!=='observing')return false;
  const end=Math.min(now,t.endsAt),long=b.direction!==-1,price=long?q?.bid:q?.ask;
  const valid=ready&&q?.status==='LIVE'&&!q.halted&&positive(q?.bid)&&positive(q?.ask)&&q.ask>=q.bid&&positive(price)&&Number.isFinite(q.at)&&q.at<=now&&now-q.at<15000;
  const at=q?.at,changedSample=valid&&at>=t.startedAt&&at<=t.endsAt&&(t.lastAt===null||at>t.lastAt);
  // A reload, suspended tab or missing feed is unknown, never a flat price path.
  if(end-t.lastCheck>15000&&t.gapStart===null)t.gapStart=t.lastCheck;
  if(!valid&&t.gapStart===null)t.gapStart=Math.min(end,t.lastCheck);
  if(end-(t.lastAt??t.startedAt)>15000&&t.gapStart===null)t.gapStart=t.lastAt??t.startedAt;
  if(changedSample){
    if(t.gapStart!==null){const to=Math.min(at,end);t.gapMs+=Math.max(0,to-t.gapStart);if(t.gaps.length<100)t.gaps.push({from:t.gapStart,to});t.gapStart=null;}
    t.samples++;t.lastAt=at;t.min=t.min===null?price:Math.min(t.min,price);t.max=t.max===null?price:Math.max(t.max,price);
    if(positive(b.target)&&(long?price>=b.target:price<=b.target)&&!t.targetHitAt)t.targetHitAt=at;
  }
  t.lastCheck=end;
  if(now>=t.endsAt){
    if(t.gapStart===null&&(t.lastAt===null||t.endsAt-t.lastAt>15000))t.gapStart=t.lastAt??t.startedAt;
    if(t.gapStart!==null){t.gapMs+=Math.max(0,t.endsAt-t.gapStart);if(t.gaps.length<100)t.gaps.push({from:t.gapStart,to:t.endsAt});t.gapStart=null;}
    t.status=t.samples?(t.gapMs?'finished with gaps':'finished (sampled quotes)'):'no data';
  }
  const exit=b.exitPrice??b.events?.filter(e=>e.qty&&e.type!=='ENTRY').at(-1)?.price,risk=b.priceR??b.initialR;
  if(t.samples&&positive(exit)){t.favorable=Math.max(0,long?t.max-exit:exit-t.min);t.adverse=Math.max(0,long?exit-t.min:t.max-exit);t.extraR=positive(risk)?t.favorable/risk:null;}
  return changedSample||t.status!=='observing'||!valid;
}
function create(a){
  const state=()=>a.state();
  function book(){const s=state();return s.desk||(s.desk={settings:{},active:[],journal:[]});}
  const settings=()=>Object.assign({},defaults,book().settings);
  const pending=()=>state().orders.filter(o=>o.status==='working'&&o.desk?.role==='entry');
  const exposure=()=>book().active.some(b=>b.managed)||pending().length>0;
  function plan(inst,spec,data){const acc=a.account();return calculate({mode:a.mode(),inst,spec,data,equity:acc.netLiq,bp:Math.max(0,acc.bp),fee:(n,p,side)=>a.commission(inst,n,p,side),now:a.now()});}
  function arm(inst,p,bookId){
    if(bookId!==state().bookId||p.mode!==a.mode())throw Error('Portfolio changed; reopen the preview');
    if(a.position(inst.conid)?.qty||state().orders.some(o=>o.status==='working'&&(o.conid===inst.conid||o.legs?.some(l=>l.conid===inst.conid))))throw Error('This stock already has a position or working order');
    if(!a.usingTws()||!a.ready(inst.conid))throw Error('Fresh live IB Gateway quote required');
    if(state().positions.some(x=>x.qty&&!a.ready(x.conid)))throw Error('Held positions need fresh marks for risk sizing');
    const o={...inst,id:a.uid(),ts:a.now(),day:a.today(),side:'BUY',qty:p.qty,filledQty:0,avgFill:0,commission:0,
      type:p.type,limit:p.entry,stop:p.type==='STPLMT'?p.entry:null,tif:'DAY',status:'working',triggered:false,
      strategyType:p.strategy,priceSource:'FULL_SIZE_PAPER',desk:{role:'entry',bookId,plan:JSON.parse(JSON.stringify(p))}};
    state().orders.unshift(o);a.save();a.sync();return o;
  }
  function guard(o){
    if(o.desk)return true;
    const owned=new Set([...book().active.filter(b=>b.managed).map(b=>b.inst.conid),...pending().map(o=>o.conid)]);
    if((o.legs||[o]).some(l=>owned.has(l.conid)&&(o.legs||l.side!=='SELL'||o.qty-o.filledQty>(a.position(l.conid)?.qty||0)))){
      o.status='rejected';o.note='Managed paper bracket owns this stock; close or cancel it first';a.save();return false;
    }return true;
  }
  function fill(o){
    if(!o.desk)return null;if(o.desk.role!=='entry'||o.status!=='working')return false;
    if(o.desk.bookId!==state().bookId){o.status='cancelled';return true;}
    if(!a.usingTws()||!a.ready(o.conid)||state().positions.some(x=>x.qty&&!a.ready(x.conid)))return false;
    const q=a.quotes()[o.conid],p=o.desk.plan;
    if(q?.status!=='LIVE'||!(q.askSize>0)||!(q.bid>0)||q.ask<q.bid||!positive(q.ask))return false;
    if(o.type==='STPLMT'&&!o.triggered){const last=Object.hasOwn(q,'tradeLast')?q.tradeLast:q.last;if(!positive(last)||last<p.entry)return false;o.triggered=true;a.save();}
    if(q.ask>p.entry||(p.stop!==null&&q.ask<=p.stop)||(p.target!==null&&q.ask>=p.target))return false;
    if(a.position(o.conid)?.qty){o.status='cancelled';o.note='Position changed';return true;}
    let qty=p.qty;
    if(p.mode==='Trading'){const acc=a.account();qty=Math.min(qty,C.size(acc.netLiq,p.riskPct,q.ask,p.stop,Math.max(0,acc.bp),n=>a.commission(o,n,q.ask,'BUY')+a.commission(o,n,p.stop,'SELL')).qty);}
    if(qty<=0){o.status='cancelled';o.note='Risk / buying power exhausted';return true;}
    o.qty=qty;o.desk.budgetAtFill=p.mode==='Trading'?a.account().netLiq*p.riskPct/100:null;
    a.fill(o,qty,q.ask);a.save();return true;
  }
  function after(o,n,price,oldQty,newQty,realized,fee,oldCost){
    if(o.guns)return;
    const d=book(),now=a.now(),signed=o.side==='BUY'?n:-n,dir=oldQty===0?Math.sign(signed):Math.sign(oldQty);
    let b=d.active.find(x=>x.inst.conid===o.conid);
    if(!b&&oldQty!==0){
      b={id:a.uid(),inst:{conid:o.conid,symbol:o.symbol,secType:o.secType,mult:o.mult||1,exch:o.exch,brokerId:o.brokerId},direction:dir,entry:oldCost,qty:Math.abs(oldQty),originalQty:Math.abs(oldQty),openedAt:null,
        stop:null,target:null,fees:0,gross:0,net:0,exitValue:0,exitQty:0,events:[],strategy:'Unknown (pre-existing position)',managed:false,coverage:'Opening fills/fees unavailable',trackMinutes:settings().trackMinutes};d.active.push(b);
    }
    function open(count,allocatedFee){
      const p=o.desk?.plan||{},managed=o.desk?.role==='entry';
      const row={id:a.uid(),inst:{conid:o.conid,symbol:o.symbol,secType:o.secType,mult:o.mult||1,exch:o.exch,brokerId:o.brokerId},
        direction:Math.sign(signed),entry:price,qty:count,originalQty:count,openedAt:now,stop:p.stop??null,originalStop:p.stop??null,target:p.target??null,originalTarget:p.target??null,
        priceR:p.stop==null?null:price-p.stop,budget:o.desk?.budgetAtFill??null,timeframe:p.timeframe??null,atr:p.atr??null,
        strategy:o.strategyType||p.strategy||'Untagged',mode:a.mode(),managed,trackMinutes:p.trackMinutes||settings().trackMinutes,
        fees:allocatedFee,gross:0,net:-allocatedFee,exitValue:0,exitQty:0,coverage:'Recorded from entry',events:[{at:now,type:'ENTRY',price,qty:count,fee:allocatedFee}]};
      d.active.push(row);return row;
    }
    if(oldQty===0){open(n,fee);return;}
    if(Math.sign(signed)===dir){
      b.entry=(b.entry*b.qty+price*n)/(b.qty+n);b.qty+=n;b.originalQty+=n;b.fees+=fee;b.net=b.gross-b.fees;
      b.events.push({at:now,type:'ADD',price,qty:n,fee,strategy:o.strategyType||null});return;
    }
    const closed=Math.min(Math.abs(oldQty),n),exitFee=fee*closed/n;
    b.gross+=(price-oldCost)*closed*dir*(o.mult||1);b.fees+=exitFee;b.qty-=closed;b.exitQty+=closed;b.exitValue+=price*closed;b.net=b.gross-b.fees;
    b.events.push({at:now,type:o.desk?.reason||'MANUAL EXIT',price,qty:closed,fee:exitFee});
    if(b.qty<=0){b.qty=0;b.closedAt=now;b.exitPrice=b.exitValue/b.exitQty;b.outcome=o.desk?.reason||'MANUAL EXIT';b.budgetR=positive(b.budget)?b.net/b.budget:null;
      d.active=d.active.filter(x=>x!==b);d.journal.unshift(b);beginTracking(b,now,b.trackMinutes);a.sync();
      state().orders.forEach(x=>{if(x!==o&&x.status==='working'&&x.conid===o.conid&&x.side===o.side){x.status='cancelled';x.note='Position closed; sibling exit cancelled';}});
    }
    if(n>closed)open(n-closed,fee-exitFee);
  }
  function manage(cid){let changed=false;for(const b of [...book().active]){
    if(!b.managed||(cid!=null&&cid!==b.inst.conid)||!a.usingTws()||!a.ready(b.inst.conid))continue;
    const q=a.quotes()[b.inst.conid];if(q?.status!=='LIVE'||!positive(q.bid)||!positive(q.ask)||q.ask<q.bid)continue;
    const reason=b.forceExit?'MANUAL FLATTEN':b.stopTriggered||(b.stop!==null&&q.bid<=b.stop)?'STOP':b.target!==null&&q.bid>=b.target?'TARGET':null;
    if(!reason)continue;if(reason==='STOP'&&!b.stopTriggered){b.stopTriggered=true;changed=true;}
    if(!(q.bidSize>0))continue;
    const count=Math.min(b.qty,Math.max(0,a.position(b.inst.conid)?.qty||0));if(!count)continue;
    const o={...b.inst,id:a.uid(),ts:a.now(),day:a.today(),side:'SELL',qty:count,filledQty:0,type:'MKT',tif:'DAY',status:'working',commission:0,
      strategyType:b.strategy,priceSource:'FULL_SIZE_PAPER',desk:{role:'exit',bookId:state().bookId,reason}};
    state().orders.unshift(o);a.fill(o,count,reason==='TARGET'?b.target:q.bid);changed=true;
  }if(changed)a.save();return changed;}
  function setTarget(id,value){const b=book().active.find(b=>b.id===id&&b.managed);if(!b)throw Error('Position is no longer open');const price=Number(value);if(!positive(price)||price<=b.entry)throw Error('TP must be above entry');
    b.events.push({at:a.now(),type:'TARGET EDIT',previous:b.target,price});b.target=price;a.save();manage(b.inst.conid);}
  function flatten(id){const b=book().active.find(b=>b.id===id&&b.managed);if(b){b.forceExit=true;a.save();manage(b.inst.conid);}}
  function tracked(){const s=state(),out=[...book().journal];for(const other of s.books||[])if(other.id!==s.bookId)out.push(...(other.data?.desk?.journal||[]));for(const gb of Object.values(s.guns?.books||{}))out.push(...(gb.journal||[]));return out.filter(b=>b.tracking?.status==='observing');}
  function track(){let changed=false;for(const b of tracked())changed=observe(b,a.quotes()[b.inst.conid],a.usingTws()&&a.ready(b.inst.conid),a.now())||changed;if(changed)a.save();}
  function instruments(){return tracked().map(b=>({...b.inst,priority:30}));}
  function onGunsClose(b){beginTracking(b,a.now(),settings().trackMinutes);a.sync();}
  return {book,settings,plan,arm,guard,fill,after,manage,setTarget,flatten,pending,exposure,track,instruments,onGunsClose};
}
return {defaults,atrFor,calculate,beginTracking,observe,create};
});
