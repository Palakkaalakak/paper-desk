"""BPSX: one chart - bid/ask spread vs profit ($) AND win rate, course baselines (OPTIONS_MASTER_DOCUMENT.md).
$100 stock, credit $0.50/share ($50/contract), win +0.9x credit, loss 3x credit (managed), realistic win rate 85%,
spread rule 0.4% of share price per option, fills near mid (course-observed: gives up ~7% of the natural-to-mid distance)."""
import numpy as np, matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
plt.rcParams['text.usetex']=False
plt.rcParams['text.parse_math']=False
PRICE,C,TP,LOSS,EXIT_TP,WIN=100.0,0.50,0.9,3.0,0.25,0.85
def parts(width,phi):
    entry=phi*width; gain=TP*C-entry-EXIT_TP*phi*width; lose=LOSS*C+entry+phi*width
    return gain,lose
def ev(width,phi,win=WIN): g,l=parts(width,phi); return (win*g-(1-win)*l)*100          # $ per contract
def breakeven(width,phi): g,l=parts(width,phi); return l/(g+l)*100
x=np.linspace(0,1.2,241)                      # spread as % of share price
w=x/100*PRICE
PHI=0.07                                      # course-observed fill: near mid
fig,ax=plt.subplots(figsize=(10.5,5.8)); ax2=ax.twinx()
ax.plot(x,[ev(v,PHI) for v in w],color='#2a6fdb',lw=3,label='Profit per trade ($ per contract) - left axis')
ax2.plot(x,[breakeven(v,PHI) for v in w],color='#e08a00',lw=3,label='Win rate needed to break even (%) - right axis')
ax2.axhline(85,color='#555',ls=':',lw=1.6);ax2.text(1.19,85.5,'course realistic win rate: 85%',ha='right',fontsize=9.5,color='#333')
ax.axhline(0,color='k',lw=1);ax.axvline(0.4,color='#2a9d8f',lw=2)
ax.text(0.41,19.5,'course limit: 0.4% of price\n($0.40 on a $100 stock)',color='#1e7a6f',fontsize=10,va='top')
ax.set_xlabel('Bid/ask spread per option, % of share price')
ax.set_ylabel('Profit per trade, $ per contract',color='#2a6fdb');ax2.set_ylabel('Win rate needed to break even (%)',color='#e08a00')
ax.set_ylim(0,20);ax2.set_ylim(70,90)
ax.set_title('BPSX: spread vs profit and the win rate you need\n(course baselines: $0.50 credit, take profit 90%, managed loss 3x credit, fills near mid)',fontsize=11.5)
h1,l1=ax.get_legend_handles_labels();h2,l2=ax2.get_legend_handles_labels()
ax.legend(h1+h2,l1+l2,fontsize=9.5,loc='lower right');ax.grid(alpha=.25)
fig.tight_layout();fig.savefig('docs/spread_vs_profit.png',dpi=130)
print('spread%  $/opt  profit$ (near-mid)  breakeven win%   | profit$ (25%)  breakeven win%')
for p in (0,.2,.4,.6,.8,1.0,1.2):
    v=p/100*PRICE;print(p,round(v,2),round(ev(v,.07),1),round(breakeven(v,.07),1),'|',round(ev(v,.25),1),round(breakeven(v,.25),1))
