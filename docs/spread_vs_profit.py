"""BPSX spread vs profitability, computed by the paper engine's own fill model (docs/spread_model.cjs -> bpsx.js).
Course baselines: $100 stock, $0.50 mid credit, worked from mid (-1c every 2 min, max 3c), TP at 10% of credit,
managed loss 3x credit plus a marketable stop exit, 85% realistic win rate."""
import json, subprocess, matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
plt.rcParams['text.parse_math']=False
d=json.loads(subprocess.check_output(['node','docs/spread_model.cjs']))
x=[r['pct'] for r in d]
fig,ax=plt.subplots(figsize=(10.5,5.8));ax2=ax.twinx()
ax.plot(x,[r['evTrade'] for r in d],color='#2a6fdb',lw=3,label='Profit per trade, $ per contract (left)')
ax2.plot(x,[r['breakeven'] for r in d],color='#e08a00',lw=3,label='Win rate needed to break even, % (right)')
ax2.axhline(85,color='#555',ls=':',lw=1.5);ax2.text(0.99,85.5,'course realistic win rate 85%',ha='right',fontsize=9.5,color='#333')
ax.axvline(0.4,color='#2a9d8f',lw=2);ax.text(0.41,17,'course allowance: 0.4% of price',color='#1e7a6f',fontsize=9.5,va='top')
ax.set_ylim(0,18);ax2.set_ylim(70,90)
ax.set_xlabel('Bid/ask spread per option, % of share price ($ on a $100 stock)')
ax.set_ylabel('Profit per trade, $ per contract',color='#2a6fdb');ax2.set_ylabel('Win rate needed to break even, %',color='#e08a00')
ax.set_title('BPSX: spread vs profit under the paper fill rule\n(instant fill at mid minus 40% of the mid-to-natural distance, on entries and stops)',fontsize=11)
h1,l1=ax.get_legend_handles_labels();h2,l2=ax2.get_legend_handles_labels();ax.legend(h1+h2,l1+l2,fontsize=9,loc='lower left');ax.grid(alpha=.25)
fig.tight_layout();fig.savefig('docs/spread_vs_profit.png',dpi=130)
