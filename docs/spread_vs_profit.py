"""Spread vs profit for BPSX, using the course payoff arithmetic (OPTIONS_MASTER_DOCUMENT.md).
r = H/credit, H = (short width + long width)/2 = natural-to-mid distance. Credit normalised to 1."""
import numpy as np, matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

TP, LOSS, EXIT_TP = 0.9, 2.0, 0.25   # take-profit share, avg loss (x credit), TP exit crosses 25% as wide (near-worthless)
def ev(r, phi, win, loss=LOSS):
    entry = phi*r                      # credit given up crossing from mid at entry
    tp_exit = EXIT_TP*phi*r            # closing at ~1-3c debit
    stop_exit = phi*r                  # stop exit is a marketable buyback
    gain = TP - entry - tp_exit
    lose = loss + entry + stop_exit
    return win*gain - (1-win)*lose
def breakeven_win(r, phi, loss=LOSS):
    entry=phi*r; gain=TP-entry-EXIT_TP*phi*r; lose=loss+entry+phi*r
    return lose/(gain+lose)

r = np.linspace(0, 1.6, 200)
fig, ax = plt.subplots(1, 2, figsize=(13, 5.2))
styles = [(0.0,'fill at mid (φ=0)','#2a9d8f'),(0.25,'give up 25% of half-widths','#e9c46a'),(0.5,'give up 50%','#f4a261'),(1.0,'fill at natural (φ=1)','#e76f51')]
for phi,lab,c in styles:
    ax[0].plot(r*100, [ev(x,phi,0.85)*100 for x in r], color=c, lw=2.2, label=lab)
ax[0].axhline(0,color='k',lw=1);ax[0].axvline(80,color='grey',ls='--');ax[0].text(81,ax[0].get_ylim()[1]*.9,'course "marginal"\n40c on 50c = 80%',fontsize=9,va='top')
ax[0].set_xlabel('Spread: natural-to-mid distance as % of mid credit');ax[0].set_ylabel('Expected profit per trade (% of credit)')
ax[0].set_title('Profit per trade at 85% win rate (course realistic)');ax[0].legend(fontsize=9);ax[0].grid(alpha=.25)
for phi,lab,c in styles:
    ax[1].plot(r*100, [breakeven_win(x,phi)*100 for x in r], color=c, lw=2.2, label=lab)
ax[1].axhline(85,color='k',ls=':',lw=1.2);ax[1].text(2,85.6,'course realistic win rate 85%',fontsize=9)
ax[1].axhline(90,color='k',ls=':',lw=.8);ax[1].text(2,90.6,'ideal 10Δ win rate 90%',fontsize=9)
ax[1].set_ylim(60,100);ax[1].set_xlabel('Spread: natural-to-mid distance as % of mid credit');ax[1].set_ylabel('Break-even win rate (%)')
ax[1].set_title('Win rate you need just to break even');ax[1].grid(alpha=.25)
fig.suptitle('BPSX: how the bid/ask spread eats the edge (win +0.9×credit, loss ≈ −2×credit, 85% win rate)',fontsize=12)
fig.tight_layout();fig.savefig('docs/spread_vs_profit.png',dpi=130)
print('r%  phi=0  .25  .5  1   (EV % of credit @85%)')
for x in (0,.2,.4,.6,.8,1.0,1.2):
    print(int(x*100),[round(ev(x,p,.85)*100,1) for p,_,_ in styles],' breakeven win@.25:',round(breakeven_win(x,.25)*100,1))
# zero-EV spread for each phi
for p,l,_ in styles[1:]:
    z=next(x for x in np.linspace(0,10,10001) if ev(x,p,.85)<=0);print(l,'EV hits zero at r=',round(z*100),'%')
