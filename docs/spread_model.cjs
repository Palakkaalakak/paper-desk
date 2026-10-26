// Spread -> profitability from the paper engine's own fill rule (bpsx.js fillModel).
// Course baselines: $100 stock, short put mid $1.00 / long put mid $0.50 (credit $0.50), equal leg widths.
const F=require('../bpsx.js').fillModel,out=[];
for(let pct=0.02;pct<=1.0001;pct+=0.02){const w=Math.round(pct*100)/100,q={1:{bid:1-w/2,ask:1+w/2,},2:{bid:Math.max(0,.5-w/2),ask:.5+w/2},9:{last:100}};
 const legs=[{conid:1,side:'SELL',underConid:9},{conid:2,side:'BUY',underConid:9}],cq=F.comboQuote(legs,q),e=F.expectedTrade(cq);
 out.push({pct,width:w,within:cq.within,credit:e.credit,evTrade:e.ev*100,breakeven:e.breakeven*100});}
console.log(JSON.stringify(out));
