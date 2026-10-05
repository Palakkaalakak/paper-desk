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
function sessionFor(rows,now){
 const s=(rows||[]).find(s=>Number.isFinite(s.start)&&Number.isFinite(s.end)&&C.day(s.start)===C.day(now)&&s.end>s.start),w=s&&C.premarketWindow(s);
 if(!s||!w||s.end<=now)return null;
 const start=Math.max(s.start,w.start+5.5*3600000),end=Math.min(s.end,w.start+12*3600000);
 return end>start?{start,end,day:C.day(now),source:'IB regular-session schedule'}:null;
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
  const session=sessionFor(spec.sessions||data?.sessions,now);if(!session)throw Error('Verified current regular-market session required; no extended-hours entry');
  return {mode,entry,stop,target,qty,budget,session,sessionPolicy:'RTH_ONLY_V1',riskPct:mode==='Trading'?riskPct:null,priceR:stop===null?null:entry-stop,
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
// One owner-requested local-account repair, not an execution or a deposit.
const ownerUpdateId='paperdesk-owner-results-20260930-v1';
const accountFields=['account','cash','realized','lastDay','positions','orders','trades','cashflows','equity','watchlist','log','desk'];
function requestedAccountRepair(state,now){
  if(state.ownerAccountUpdates?.[ownerUpdateId])return null;
  const normalize=x=>String(x||'').toLowerCase().replace(/[^a-z0-9]/g,''),clone=x=>JSON.parse(JSON.stringify(x));
  const next=clone(state),books=next.books;
  if(!Array.isArray(books))return null;
  const active=books.find(b=>b.id===next.bookId);if(!active)return null;
  active.data=Object.fromEntries(accountFields.filter(k=>next[k]!==undefined).map(k=>[k,clone(next[k])]));
  const named=(b,name)=>normalize(b.name)===name||normalize(b.data?.account?.name)===name;
  const targets=books.filter(b=>named(b,'paperdesk')),pam=books.filter(b=>named(b,'pam'));
  if(targets.length!==1)return null;
  const target=targets[0];if(pam.includes(target))return null;
  // Initial repair is scoped to the owner's Paper Desk + PAM state. Explicit
  // prior request evidence also permits remaining credits after PAM was removed.
  const d=target.data,prior=Object.values(next.ownerAccountUpdates||{}).some(r=>r.targetBookId===target.id);
  const requests=[['JAGX',2000],['USDE',2040],['CYPH',2040]];
  const requestId=s=>'owner-reported-'+s+'-'+requests.find(x=>x[0]===s)[1]+'-v1';
  const existing=[...(d?.desk?.journal||[]),...(next.guns?.books?.[target.id]?.journal||[])];
  const identified=existing.some(b=>requests.some(([s])=>b.id===requestId(s)||b.requestId===requestId(s)));
  if(!pam.length&&!prior&&!identified)return null;
  if(!d?.account||!Number.isFinite(d.cash)||!Number.isFinite(d.realized))throw Error('Paper Desk has invalid accounting; owner update stopped');
  for(const k of ['positions','orders','trades','cashflows','equity','watchlist','log']){
    if(d[k]===undefined)d[k]=[];
    if(!Array.isArray(d[k]))throw Error('Invalid Paper Desk '+k+'; owner update stopped');
  }
  d.desk=d.desk||{settings:{},active:[],journal:[]};
  if(!Array.isArray(d.desk.journal))throw Error('Invalid Paper Desk journal; owner update stopped');
  const report={id:ownerUpdateId,at:now,targetBookId:target.id,deletedBookIds:pam.map(b=>b.id),delta:0,results:[]};
  for(const [symbol,net] of requests){
    const id=requestId(symbol),jagx=symbol==='JAGX';
    const matches=existing.filter(b=>b.id===id||b.requestId===id||(
      b.inst?.symbol===symbol&&/owner.reported|user.reported/i.test(b.strategy||'')&&
      (jagx?b.entry===10.40&&b.originalStop===9.96&&(b.originalTarget??b.target)===11.28&&/20:55/.test(b.reportedEntryTime||''):b.requestedNet===net)
    ));
    const ledger=d.trades.filter(t=>t.id===id||t.requestId===id);
    if(matches.length>1||ledger.length>1)throw Error('Ambiguous existing '+symbol+' owner credit; update stopped');
    const recorded=matches.length?matches[0].net:ledger.length?ledger[0].realized:0;
    if(!Number.isFinite(recorded)||recorded<0||recorded>net)throw Error('Conflicting existing '+symbol+' owner credit; update stopped');
    if(matches.length&&ledger.length&&matches[0].net!==ledger[0].realized)throw Error('Journal/ledger mismatch for '+symbol+'; update stopped');
    const delta=Math.round((net-recorded)*100)/100;
    report.results.push({requestId:id,symbol,net,previouslyRecorded:recorded,delta,status:delta?'credited':'already recorded'});
    if(!delta)continue;
    const basis=jagx?'Owner-reported +2R normalized at $1,000/R = $2,000; not share-based execution':'Owner-reported net profit of $2,040; risk budget not supplied';
    const adjustmentId=recorded?id+'-remainder':id;
    d.cash=Math.round((d.cash+delta)*100)/100;d.realized=Math.round((d.realized+delta)*100)/100;report.delta+=delta;
    d.desk.journal.unshift({id:adjustmentId,requestId:id,inst:{symbol,secType:'STK',mult:1},qty:0,originalQty:null,
      openedAt:null,closedAt:null,recordedAt:now,entry:jagx?10.40:null,originalStop:jagx?9.96:null,stop:jagx?9.96:null,
      target:jagx?11.28:null,originalTarget:jagx?11.28:null,exitPrice:null,budget:jagx?1000:null,budgetR:jagx?delta/1000:null,
      net:delta,requestedNet:net,previouslyRecorded:recorded,gross:null,fees:null,managed:false,
      strategy:jagx?'GUNS S1 · owner-reported':'Owner-reported result',reportedEntryTime:jagx?'20:55 Asia/Bangkok; date not supplied':'unknown',
      outcome:'PROFIT — owner-reported adjustment',coverage:basis+'; trade date, quantity, fees and exit time unknown; NOT a broker fill',repairId:ownerUpdateId,
      events:[{at:now,type:'OWNER-REQUESTED CREDIT',detail:basis+'; previously recorded '+recorded+'; added '+delta}]});
    d.trades.unshift({id:adjustmentId,requestId:id,ts:now,orderId:adjustmentId,symbol,secType:'STK',side:'ADJUST',qty:0,price:null,
      commission:null,realized:delta,cashAfter:d.cash,mult:1,priceSource:'USER_REPORTED_RESULT',repairId:ownerUpdateId,combo:basis});
  }
  d.log.unshift({ts:now,text:'Owner-requested Paper Desk adjustment +$'+report.delta.toFixed(2)+' (JAGX / USDE / CYPH). PAM deleted without transferring money, positions, orders or trades. Original account backed up before persistence.'});
  next.books=books.filter(b=>!pam.includes(b));
  for(const b of pam)if(next.guns?.books)delete next.guns.books[b.id];
  next.bookId=target.id;next.lastSelected=null;
  for(const k of accountFields){delete next[k];if(d[k]!==undefined)next[k]=clone(d[k]);}
  next.ownerAccountUpdates=next.ownerAccountUpdates||{};next.ownerAccountUpdates[ownerUpdateId]=report;
  return {state:next,report};
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
    if(p.sessionPolicy!=='RTH_ONLY_V1'||!sessionFor([p.session],a.now()))throw Error('Regular session changed; reopen the preview');
    if(a.now()>=p.session.start&&(!a.usingTws()||!a.ready(inst.conid)))throw Error('Fresh live IB Gateway quote required');
    if(state().positions.some(x=>x.qty&&!a.ready(x.conid)))throw Error('Held positions need fresh marks for risk sizing');
    const o={...inst,id:a.uid(),ts:a.now(),day:a.today(),side:'BUY',qty:p.qty,filledQty:0,avgFill:0,commission:0,
      type:p.type,limit:p.entry,stop:p.type==='STPLMT'?p.entry:null,tif:'DAY',status:'working',triggered:false,
      strategyType:p.strategy,priceSource:'FULL_SIZE_PAPER',desk:{role:'entry',bookId,paused:true,plan:JSON.parse(JSON.stringify(p))}};
    state().orders.unshift(o);a.save();a.sync();return o;
  }
  function startEntry(id){const o=pending().find(o=>o.id===id);if(!o)return;
    if(o.desk.plan.sessionPolicy!=='RTH_ONLY_V1'||!sessionFor([o.desk.plan.session],a.now()))throw Error('Cancel this old entry and prepare again with a verified regular session');
    o.desk.paused=false;a.save();
  }
  function pauseEntry(id){const o=pending().find(o=>o.id===id);if(o){o.desk.paused=true;a.save();}}
  function cancelEntry(id){const o=pending().find(o=>o.id===id);if(!o)return false;o.status='cancelled';o.note='Cancelled by user before fill';a.save();a.sync();return true;}
  function entryStatus(o){const p=o.desk.plan;
    if(p.sessionPolicy!=='RTH_ONLY_V1'||!p.session)return 'Old entry blocked — cancel and prepare again';
    if(o.desk.paused)return 'PAUSED — Start entry or Cancel entry';
    if(a.now()<p.session.start)return 'QUEUED — waiting for regular open (no premarket trigger/fill)';
    return o.triggered?'TRIGGERED — waiting within limit':'WORKING — waiting for trigger/limit and a safe live spread';
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
    const p=o.desk.plan,now=a.now();
    if(p.sessionPolicy!=='RTH_ONLY_V1'||!p.session)return false;
    if(now>=p.session.end||C.day(now)!==p.session.day){o.status='cancelled';o.note='Expired at regular-session close (DAY)';a.save();return true;}
    if(o.desk.paused||now<p.session.start)return false;
    if(!a.usingTws()||!a.ready(o.conid)||state().positions.some(x=>x.qty&&!a.ready(x.conid)))return false;
    const q=a.quotes()[o.conid];
    if(q?.status!=='LIVE'||q.error||q.halted||!Number.isFinite(q.at)||q.at>now||now-q.at>=15000||!(q.askSize>0)||!(q.bid>0)||q.ask<q.bid||!positive(q.ask))return false;
    if(p.stop!==null&&q.bid<=p.stop)return false; // no entry when this spread already trips its SL
    if(o.type==='STPLMT'&&!o.triggered){const last=Object.hasOwn(q,'tradeLast')?q.tradeLast:q.last;if(!positive(last)||last<p.entry)return false;o.triggered=true;a.save();}
    if(q.ask>p.entry||(p.stop!==null&&q.ask<=p.stop)||(p.target!==null&&q.ask>=p.target))return false;
    if(a.position(o.conid)?.qty){o.status='cancelled';o.note='Position changed';return true;}
    let qty=p.qty;
    if(p.mode==='Trading'){const acc=a.account();qty=Math.min(qty,C.size(acc.netLiq,p.riskPct,q.ask,p.stop,Math.max(0,acc.bp),n=>a.commission(o,n,q.ask,'BUY')+a.commission(o,n,p.stop,'SELL')).qty);}
    if(qty<=0||qty*q.ask+a.commission(o,qty,q.ask,'BUY')>a.account().bp){o.status='cancelled';o.note='Risk / buying power exhausted';a.save();return true;}
    o.qty=qty;o.desk.budgetAtFill=p.mode==='Trading'?a.account().netLiq*p.riskPct/100:null;
    if(a.fill(o,qty,q.ask)===false)return false;a.save();return true;
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
  // Post-exit observation must not displace active execution (priority 3).
  function instruments(){return tracked().map(b=>({...b.inst,priority:1}));}
  function onGunsClose(b){beginTracking(b,a.now(),settings().trackMinutes);a.sync();}
  return {book,settings,plan,arm,startEntry,pauseEntry,cancelEntry,entryStatus,guard,fill,after,manage,setTarget,flatten,pending,exposure,track,instruments,onGunsClose};
}
return {defaults,ownerUpdateId,requestedAccountRepair,sessionFor,atrFor,calculate,beginTracking,observe,create};
});
