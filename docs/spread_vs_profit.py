"""BPSX spread vs profit using the COURSE baselines (OPTIONS_MASTER_DOCUMENT.md, BPSX chapter).
Credit $0.50/share (Netflix, Alibaba, TAL examples: 0.45-0.96); win +0.9*credit (Rule 1); managed loss -3*credit
(Rule 4: "you lose three times your money"; Rule 5 exits usually cost 1-2x); win rate 80/85/90% (85% realistic);
bid/ask guide under $0.30-0.40 per option; fills near mid (TAL: IB default 0.30, mid 0.45, filled 0.44-0.45;
Netflix quote 4.50 filled 4.40); closing debits 1-3 cents.  Both legs assumed equally wide: H = width (natural-to-mid)."""
import numpy as np, matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
C=0.50; TP=0.9; EXIT_TP=0.25
def ev(width, phi, win, loss=3.0):
    H=width                         # (short width + long width)/2 with equal legs
    entry=phi*H; gain=TP*C-entry-EXIT_TP*phi*H; lose=loss*C+entry+phi*H
    return win*gain-(1-win)*lose    # $ per share
w=np.linspace(0,0.8,161)
fig,ax=plt.subplots(1,2,figsize=(13,5.3))
fills=[(0.0,'fill at mid','#2a9d8f'),(0.07,'course-observed fill (TAL: 1c of 15c)','#4c78a8'),(0.25,'give up 25%','#e9c46a'),(1.0,'fill at natural price','#e76f51')]
for phi,lab,c in fills: ax[0].plot(w,[ev(x,phi,.85)*100 for x in w],color=c,lw=2.2,label=lab)
ax[1].set_title('Win-rate sensitivity, course-observed fill (loss = 3× credit)')
for win,c in [(.80,'#e76f51'),(.85,'#4c78a8'),(.90,'#2a9d8f')]: ax[1].plot(w,[ev(x,.07,win)*100 for x in w],color=c,lw=2.2,label=f'{int(win*100)}% win rate')
for loss,ls in [(2.0,'--'),(1.0,':')]: ax[1].plot(w,[ev(x,.07,.85,loss)*100 for x in w],color='#4c78a8',ls=ls,lw=1.4,label=f'85%, avg loss {loss:.0f}× (Rule 5 exits)')
for a in ax:
    a.axhline(0,color='k',lw=1);a.axvspan(.30,.40,color='grey',alpha=.18);pass
    a.set_xlabel('Quoted bid/ask width per option ($), both legs equal');a.set_ylabel('Expected profit per trade (¢ per share, $0.50 credit)');a.grid(alpha=.25)
ax[0].set_title('Fill behaviour (85% win rate, loss = 3× credit)')
ax[0].legend(fontsize=8.5,loc='lower left');ax[1].legend(fontsize=8.5,loc='upper right')
for a in ax: a.annotate('course guide\n$0.30–0.40',xy=(.35,a.get_ylim()[0]+(a.get_ylim()[1]-a.get_ylim()[0])*.04),ha='center',va='bottom',fontsize=8.5)
fig.suptitle('BPSX: bid/ask width vs expected profit — course baselines (credit $0.50, TP 90%, loss 3×, win 85%)',fontsize=12)
fig.tight_layout();fig.savefig('docs/spread_vs_profit.png',dpi=130)
print('width$ | mid | observed | 25% | natural   (cents/share, 85% win, 3x loss)')
for x in (0,.1,.2,.3,.4,.5,.6,.8): print(x,[round(ev(x,p,.85)*100,1) for p,_,_ in fills])
print('zero-EV width:',{l:next((round(x,2) for x in np.linspace(0,3,3001) if ev(x,p,.85)<=0),None) for p,l,_ in fills})
print('win rate needed at 0.40 width:',{l:(round(next((wr for wr in np.linspace(.5,1,5001) if ev(.4,p,wr)>=0),float('nan'))*100,1)) for p,l,_ in fills})
print('win rate needed at 0 width:',round(next(wr for wr in np.linspace(.5,1,5001) if ev(0,0,wr)>=0)*100,1))
