/* Vanilla-JS local paper order preview and journal. */
(function(root){'use strict';
root.PaperTradingUI=function(a,E){
  const esc=a.esc,money=x=>Number.isFinite(x)?a.money(x):'unknown',px=x=>Number.isFinite(x)?a.px(x):'—';
  const frames=value=>[['1','1 minute'],['5','5 minutes'],['15','15 minutes'],['d','Daily']].map(([v,label])=>'<option value="'+v+'" '+(v===value?'selected':'')+'>'+label+'</option>').join('');
  const input=(id,label,value,extra='')=>'<label>'+label+'<input id="desk-'+id+'" value="'+esc(value??'')+'" '+extra+'></label>';
  function active(){return E.book().active.filter(b=>b.managed).map(b=>'<article><b>'+esc(b.inst.symbol)+'</b> · '+b.qty+' shares · '+esc(b.strategy)+' · Entry '+px(b.entry)+' / SL '+px(b.stop)+' / TP '+(b.target===null?'not set':px(b.target))+' <button data-desk-target="'+esc(b.id)+'">Set / edit TP</button> <button data-desk-flat="'+esc(b.id)+'">Flatten at live quote</button></article>').join('')+E.pending().map(o=>'<article>'+esc(o.symbol)+' · pending '+o.qty+' shares @ '+px(o.limit)+' · '+esc(o.strategyType)+' <button data-desk-cancel="'+esc(o.id)+'">Cancel</button></article>').join('');}
  function panel(){const s=E.settings(),mode=a.mode();return '<section class="panel desk-panel"><h2>'+esc(mode)+' paper workspace</h2>'+
    (mode==='GUNS'?'<p>GUNS keys remain 1–5. Tracking below also applies to newly closed GUNS trades.</p>':'<p>Selected stock → <b>1</b> prepares → <b>Enter</b> confirms → <b>Esc</b> cancels. '+(mode==='Trading'?'Blank SL = 1 ATR of the selected timeframe; automatic TP defaults to 2R, or choose TP later.':'No automatic ATR, quantity or target calculations. Enter your quantity and optional SL/TP.')+'</p><button id="desk-prepare">Prepare selected stock · 1</button>')+
    '<div class="guns-fields">'+input('strategy','Strategy type / tag (manual ticket & next preview)',s.strategy,'maxlength="120" data-desk-setting="strategy"')+
    input('tracking','Post-exit observation minutes (1–30)',s.trackMinutes,'type="number" min="1" max="30" step="1" data-desk-setting="trackMinutes"')+'</div><p class="note">Keep browser and Gateway running. Only observed live quotes are recorded; no reconstruction through disconnections. Tracking continues across portfolio switches in this browser.</p><div id="desk-active">'+active()+'</div></section>';}
  function tracking(b){const t=b.tracking;if(!t)return '<p>Post-exit observations not recorded for this historical trade.</p>';
    return '<p>Post-exit '+t.minutes+'m · <b>'+esc(t.status)+'</b> · '+t.samples+' distinct live quotes · range '+px(t.min)+'–'+px(t.max)+' · favorable '+px(t.favorable)+' / adverse '+px(t.adverse)+' per share · additional price R '+(Number.isFinite(t.extraR)?t.extraR.toFixed(3):'unknown')+' · later TP '+(t.targetHitAt?'observed '+esc(new Date(t.targetHitAt).toLocaleTimeString()):'not observed (not proof of no hit)')+'</p><p class="note">Missing observation time: '+Math.round((t.gapMs+(t.gapStart===null?0:Math.max(0,Math.min(Date.now(),t.endsAt)-t.gapStart)))/1000)+'s. Sampled quotes are not the full market path; additional price R excludes fees.</p>';
  }
  function journal(){const rows=[...E.book().journal,...(a.gunsJournal?.()||[])].sort((x,y)=>(y.closedAt||0)-(x.closedAt||0));
    return '<section class="panel desk-journal"><h2>Detailed trade journal</h2><p>New non-GUNS fills are grouped from opening to flat, including partial exits. Existing history stays intact; unknown historical risk and fees are not invented.</p>'+rows.slice(0,200).map(b=>{
      const budget=b.correction?.riskBudget??b.budget??b.budgetAtFill,r=budget>0?b.net/budget:null,events=b.events||[],guns=b.setup!=null;
      return '<article><h3>'+esc(b.inst.symbol)+' · '+esc(b.strategy||(guns?'GUNS S'+b.setup:'Untagged'))+' · '+money(b.net)+' / '+(r===null?'unknown':r.toFixed(4))+' budget R</h3>'+
        '<p>Entry time '+esc(b.openedAt?new Date(b.openedAt).toLocaleString():'unknown')+' → Exit time '+esc(new Date(b.closedAt).toLocaleString())+' · '+esc(b.correction?'TARGET (corrected)':b.outcome||events.filter(e=>e.qty&&e.type!=='ENTRY').at(-1)?.type||'unknown')+'</p>'+
        '<p>Entry '+px(b.entry)+' · Exit '+px(b.correction?.exitPrice??b.exitPrice??events.filter(e=>e.qty&&e.type!=='ENTRY').at(-1)?.price)+' · Original SL '+px(b.originalStop)+' · Original TP '+px(b.originalTarget??b.plan?.target)+' · Current TP '+px(b.target)+' · '+b.originalQty+' shares/contracts</p>'+
        '<p>Timeframe '+esc(b.timeframe||(guns?'strategy-defined':'unknown'))+' · ATR '+px(b.atr?.value)+' · Risk budget '+money(budget)+' · Fees '+money(b.fees)+' · '+esc(b.coverage||b.fillModel||'')+'</p>'+tracking(b)+
        (guns?'<button data-guns-journal-edit="'+esc(b.id)+'">Correct / edit TP outcome</button>':'')+
        '<details><summary>Fill lifecycle / correction audit</summary>'+events.map(e=>'<p>'+esc(new Date(e.at).toLocaleString())+' · '+esc(e.type)+' · '+px(e.price)+' · '+(e.qty||'')+'</p>').join('')+(b.corrections||[]).map(c=>'<p>Correction: '+money(c.previousNet)+' → '+money(c.net)+' · '+esc(c.reason)+'</p>').join('')+'</details></article>';
    }).join('')+(rows.length?'':'<p>No closed trade journals yet.</p>')+'</section>';
  }
  function open(){
    if(a.mode()==='GUNS'||document.querySelector('dialog[open]'))return;
    const ctx=a.context(),inst=ctx?.inst;if(!inst){a.warn('Select a stock in the ticket or click a chart first.');return;}
    const bookId=a.state().bookId,mode=a.mode(),s=E.settings(),q=a.quotes()[inst.conid],d=document.createElement('dialog');d.id='desk-order-preview';
    d.innerHTML='<form><h2>'+esc(mode)+' · '+esc(inst.symbol)+' · paper preview</h2><p>Nothing is placed until you confirm. Long stocks only; full paper fills do not guarantee real liquidity.</p><div class="guns-fields">'+
      input('entry','Selected entry / maximum buy price',q?.ask,'type="number" step="any" min="0" required')+
      '<label>Entry behavior<select id="desk-type"><option value="LMT">Limit (ask at or below entry)</option><option value="STPLMT">Breakout stop-limit (trigger at entry)</option></select></label>'+
      '<label>Chart timeframe<select id="desk-frame">'+frames(ctx.frame||s.timeframe)+'</select></label>'+
      input('sl',mode==='Trading'?'SL (blank = 1 ATR)':'SL (optional, no auto calculation)','','type="number" step="any" min="0"')+
      input('tp','TP price (optional)','','type="number" step="any" min="0"')+
      (mode==='Trading'?input('risk','Risk / equity %',s.riskPct,'type="number" min="0.01" max="100" step="0.01" required')+input('reward','Automatic TP in R',s.rewardR,'type="number" min="0.1" step="0.1" required')+'<label class="guns-check"><input id="desk-later" type="checkbox">Entry + SL only; set TP later</label>':input('qty','Shares (required)','','type="number" min="1" step="1" required'))+
      input('tag','Strategy type / tag',s.strategy,'maxlength="120"')+input('minutes','Track after exit (minutes)',s.trackMinutes,'type="number" min="1" max="30" step="1" required')+
      '</div><p id="desk-preview-error" role="alert"></p><section id="desk-preview-result" aria-live="polite">Calculating…</section><footer><button type="button" id="desk-recalculate">Update preview</button><button type="submit" id="desk-confirm" disabled>Confirm paper order · Enter</button><button type="button" id="desk-dismiss">Cancel · Esc</button></footer></form>';
    document.body.append(d);d.showModal();const get=id=>d.querySelector('#desk-'+id),error=t=>get('preview-error').textContent=t;
    let data=ctx.data,plan=null,signature='',version=0,sent=false;
    const spec=()=>({entry:get('entry').value,type:get('type').value,timeframe:get('frame').value,stop:get('sl').value,target:get('tp').value,
      riskPct:get('risk')?.value,rewardR:get('reward')?.value,qty:get('qty')?.value,targetLater:!!get('later')?.checked,strategy:get('tag').value,trackMinutes:Number(get('minutes').value)});
    const validContext=()=>{const c=a.context();return bookId===a.state().bookId&&mode===a.mode()&&Number(c?.inst?.conid)===Number(inst.conid)&&c?.inst?.symbol===inst.symbol;};
    function show(p){get('preview-result').innerHTML='<h3>'+p.qty+' shares · '+esc(p.strategy||'Untagged')+'</h3><p>Entry '+px(p.entry)+' · SL '+px(p.stop)+' · TP '+(p.target===null?'set later':px(p.target))+'</p><p>1 budget R '+money(p.budget)+' · Funded price risk '+money(p.fundedRisk)+' · ATR '+px(p.atr?.value)+' ('+esc(p.timeframe)+') · estimated round-trip fees '+money(p.fees)+'</p>';}
    async function prepare(){const v=++version;plan=null;get('confirm').disabled=true;error('');try{
      if(!d.querySelector('form').reportValidity())return;if(!validContext())throw Error('Selection or portfolio changed; reopen the preview');
      const sp=spec(),key=JSON.stringify(sp);
      if(mode==='Trading'&&!sp.stop){get('preview-result').textContent='Loading broker candles for '+inst.symbol+'…';if(!data||data.updatedAt>Date.now()||Date.now()-data.updatedAt>15000)data=await a.bars(inst);}
      if(v!==version||!d.open)return;if(!validContext())throw Error('Selection or portfolio changed; reopen the preview');
      plan=E.plan(inst,sp,data);signature=key;show(plan);get('confirm').disabled=false;get('confirm').focus();
    }catch(e){if(v===version&&d.open){error(e.message);get('preview-result').textContent='No order placed.';}}}
    function changed(){version++;plan=null;get('confirm').disabled=true;get('preview-result').textContent='Inputs changed — update preview before confirming.';}
    d.addEventListener('input',changed);d.addEventListener('change',changed);
    d.querySelector('form').addEventListener('submit',async e=>{e.preventDefault();if(sent)return;
      if(!plan||signature!==JSON.stringify(spec())){await prepare();return;}
      try{if(!validContext())throw Error('Selection or portfolio changed; reopen the preview');
        const checked=E.plan(inst,spec(),data);
        if(checked.qty!==plan.qty||checked.budget!==plan.budget||checked.stop!==plan.stop||checked.target!==plan.target){plan=checked;show(plan);error('Equity / sizing changed. Review and press Enter again.');return;}
        E.arm(inst,plan,bookId);sent=true;
        Object.assign(E.book().settings,{riskPct:plan.riskPct||s.riskPct,rewardR:Number(spec().rewardR)||s.rewardR,timeframe:plan.timeframe,trackMinutes:plan.trackMinutes,strategy:plan.strategy});
        a.save();d.close();a.render();
      }catch(e){error(e.message);}
    });
    d.addEventListener('keydown',e=>{if(e.key==='Enter'&&e.repeat)e.preventDefault();if(e.key==='Enter'&&!e.repeat&&!plan){e.preventDefault();prepare();}});
    get('recalculate').onclick=prepare;get('dismiss').onclick=()=>d.close();d.addEventListener('close',()=>{version++;d.remove();});if(mode==='Trading')prepare();else get('preview-result').textContent='Enter your share quantity and optional levels, then update preview.';
  }
  document.addEventListener('click',e=>{if(e.target.closest('#desk-prepare')){open();return;}const t=e.target.closest('[data-desk-target],[data-desk-flat],[data-desk-cancel]');if(!t)return;
    if(t.dataset.deskCancel){a.cancel(t.dataset.deskCancel);a.render();return;}if(t.dataset.deskFlat){E.flatten(t.dataset.deskFlat);a.render();return;}
    const b=E.book().active.find(b=>b.id===t.dataset.deskTarget);if(!b)return;const value=prompt('Take-profit for '+b.inst.symbol+' (above entry '+b.entry+')',b.target??'');if(value===null)return;try{E.setTarget(b.id,value);a.render();}catch(err){a.warn(err.message);}
  });
  document.addEventListener('change',e=>{const k=e.target.dataset?.deskSetting;if(!k)return;let value=e.target.value;if(k==='trackMinutes'){value=Number(value);if(!Number.isInteger(value)||value<1||value>30){a.warn('Tracking must be 1–30 whole minutes.');return;}}E.book().settings[k]=value;a.save();});
  document.addEventListener('keydown',e=>{if(e.defaultPrevented||e.repeat||e.altKey||e.ctrlKey||e.metaKey||e.shiftKey||e.key!=='1'||document.hidden||document.querySelector('dialog[open]')||a.mode()==='GUNS')return;if(e.target.closest?.('input,textarea,select,button,[contenteditable]:not([contenteditable="false"])'))return;e.preventDefault();open();});
  return {panel,journal,tracking,open,active};
};
})(globalThis);
