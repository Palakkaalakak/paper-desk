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
return {defaults,atrFor,calculate,beginTracking,observe};
});
