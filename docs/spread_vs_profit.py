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
fig,ax=plt.subplots(figsize=(11.5,6.2)); ax2=ax.twinx()
cols={0.07:'#2a6fdb',0.25:'#e08a00'}
for phi,lab in [(0.07,'fill near mid (course-observed)'),(0.25,'give up 25% of distance to natural')]:
    ax.plot(x,[ev(v,phi) for v in w],color=cols[phi],lw=2.8,label=f'Profit per trade — {lab}')
    ax2.plot(x,[breakeven(v,phi) for v in w],color=cols[phi],lw=2,ls='--',label=f'Win rate needed to break even — {lab}')
ax2.axhline(85,color='#555',ls=':',lw=1.6);ax2.text(1.19,85.4,'course realistic win rate 85%',ha='right',fontsize=9,color='#333')
ax.axhline(0,color='k',lw=1);ax.axvline(0.4,color='#2a9d8f',lw=2);ax.axvspan(0,0.4,color='#2a9d8f',alpha=.08)
ax.text(0.405,ax.get_ylim()[1]*0.93 if False else 17.5,'course limit\n0.4% of price\n($0.40 on $100)',color='#1e7a6f',fontsize=9.5,va='top')
ax.set_xlabel('Bid/ask spread per option, % of share price   (on a $100 stock: 0.4% = $0.40)')
ax.set_ylabel('Expected profit per trade, $ per contract (credit $50)')
ax2.set_ylabel('Win rate needed to break even (%)');ax2.set_ylim(70,100)
ax.set_ylim(-30,22)
ax.set_title('BPSX — how the spread moves profit ($) and the win rate you need, together\n(85% realistic win rate · win +0.9× credit · managed loss −3× credit · $0.50 credit)',fontsize=11.5)
h1,l1=ax.get_legend_handles_labels();h2,l2=ax2.get_legend_handles_labels()
ax.legend(h1+h2,l1+l2,fontsize=8.8,loc='lower left');ax.grid(alpha=.25)
fig.tight_layout();fig.savefig('docs/spread_vs_profit.png',dpi=130)
print('spread%  $/opt  profit$ (near-mid)  breakeven win%   | profit$ (25%)  breakeven win%')
for p in (0,.2,.4,.6,.8,1.0,1.2):
    v=p/100*PRICE;print(p,round(v,2),round(ev(v,.07),1),round(breakeven(v,.07),1),'|',round(ev(v,.25),1),round(breakeven(v,.25),1))
