// Emits spread -> profitability data from the SAME fill model the paper engine uses (bpsx.js fillModel).
// Course baselines: $100 stock, short put mid $1.00 / long put mid $0.50 (credit $0.50), equal leg widths.
const F=require('../bpsx.js').fillModel,out=[];
for(let pct=0.02;pct<=1.0001;pct+=0.02){const w=Math.round(pct*100)/100,q={1:{bid:1-w/2,ask:1+w/2},2:{bid:Math.max(0,.5-w/2),ask:.5+w/2}};
 const cq=F.comboQuote([{conid:1,side:'SELL'},{conid:2,side:'BUY'}],q),e=F.expectedTrade(cq);
 const breakeven=e.loss/(e.gain+e.loss);out.push({pct,width:w,pFill:e.pFill,credit:e.credit,evTrade:e.ev*100,evAttempt:e.evPerAttempt*100,breakeven:breakeven*100});}
console.log(JSON.stringify(out));
