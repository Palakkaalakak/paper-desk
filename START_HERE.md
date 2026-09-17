# Paper Desk — local startup and GUNS user guide

Updated 2026-09-17. This guide describes the current Python / vanilla-JavaScript workstation, not a cloud service. Repository: https://github.com/Palakkaalakak/paper-desk (branch `main`).

## 1. Start safely

1. Update your local checkout with `git pull --ff-only origin main`. Do not discard your local changes if Git reports a conflict.
2. Install the pinned adapter: `python3 -m pip install -r requirements.txt` (Windows: use `python`).
3. Start TWS or IB Gateway and log in. Enable socket API connections where required and keep **Read-Only API** enabled.
4. In the Paper Desk folder, run `python3 serve.py`. On Windows, `start.bat` is an alternative; on Mac, right-click `start.command` → Open.
5. Open **http://localhost:8765**, not the HTML file. Select **Your own TWS or IB Gateway** as the market-data provider in Account if it is not already selected.
6. After an update, restart the Python server and reload the browser so backend and frontend match.

Automatic port discovery covers Gateway 4001/4002 and TWS 7496/7497. A live-account Gateway login supplies data only: Paper Desk does not send real broker orders. The adapter retains `readonly=True` and order-method seals; the server blocks broker-write routes.

**Keep the browser, server and Gateway running.** Stops, targets and fills are browser-local simulations, not broker-held orders. Sleeping or closing the browser suspends that protection. Fresh LIVE IB quotes and displayed liquidity are required for automatic market-data fills. Explicit user-entered paper BUY/SELL does not require live quotes and is labeled as assumed execution, never as a live fill. Gaps and simulated slippage can exceed the planned risk budget.

Paper accounts live in this browser's `paperAccount` localStorage. Another device/browser has a separate account. GitHub source checkpoints do not back up paper balances, orders, keys or journals. No hosted URL or cloud collector was created.


## Latest fast workflow: scanner, charts and manual trades

- Scanner universe means **US-listed, USD-denominated common stocks**, including foreign-domiciled US-listed issuers. Independent company/news search remains available.
- Check **Exclude / replace** on a scanner stock to promote the next verified reserve. Untick its saved exclusion to restore eligibility. This does not change existing positions or chart assignments. Exclusions reset on the next New York date. If reserves run out, scan again; missing slots are not padded.
- Use **Load scanner → 4 charts** for explicit four-panel loading. Premarket is shaded white from 04:00 ET to the reported regular open on intraday frames; larger bars shade only their overlapping portion. Daily has no intraday shading.
- **Manual paper BUY / SELL** lets you enter price and whole shares without spread, MA, entry-window, risk-budget or live-quote vetoes. An S button opens this ticket when its calculated plan warns. Select SELL to close held long shares; shorts are outside this ticket. Enter both optional BUY stop and target, or leave both blank. A separate **Manual paper close** button is available on protected positions.
- The ticket records your assumed price as `USER_ENTERED_PAPER`, including overridden warnings in the trade ledger. It can exceed paper buying power. It never sends an order to IB, changes quotes or claims a live market fill. Pending automatic entries are unchanged; cancel them separately if unwanted. Automatic protective exits still need live quotes.
- Gap is the current timestamped price versus the **actual previous trading session's IB RTH close**, not yesterday's arbitrary bar, bid/ask midpoint, extended-hours close or a frozen opening gap. A recent completed 1m close may be used when last-trade time is unavailable, visibly labeled as such. Inspect the displayed numerator, denominator, prior-session date and source. Split-adjusted TRADES prices are not dividend-adjusted. Missing calendar or price evidence stays unknown, but does not prevent an explicit manual paper trade.

## 2. Configure scanner reference data

**No key is required for the default share-reference source.** Paper Desk first reads the public Stock Analysis statistics page. It verifies the exact symbol, integer counts, and provider statistics update timestamp. This is a provider snapshot date, not a claimed issuer float-effective date; the UI labels that distinction. If exact float is absent, total outstanding shares may establish a conservative upper bound below the configured cap, explicitly labeled **not exact float**. Neither count is guessed from price, volume or percentages.

FMP is now an **optional fallback**, used only if the public source cannot return usable evidence. To configure it, open **GUNS → Scanner data setup / FMP key** on the computer running Paper Desk:

- Paste your FMP key into the password field and choose **Save on this Paper Desk server**.
- The field clears after submission. The key is sent in a same-origin POST body, not a URL, and saved in the server's ignored `.env` file. It is active immediately and is not stored in the browser account or committed to GitHub.
- Saving confirms local storage, **not provider validity or subscription coverage**. Use **Scan now** or reselect your company to request float again.
- Saving from another computer is rejected even if that browser can access the application. Open `localhost:8765` on the server computer instead.
- You can alternatively set `PAPER_FMP_KEY` in the Python process environment or local `.env`, then restart the server.

The share-reference endpoint requires matching-symbol count evidence and a provider update date less than 45 days old. IBKR's retired fundamental request is not used. Live probes on 2026-09-16 returned public float for MEDS, ZTG, RETO, WAFU, CYPH and FTFT; YFOR returned a total-outstanding-share upper bound. All seven had returned FMP HTTP 402 with the supplied subscription, while AAPL succeeded. The default public source avoids that subscription dependency. **Universal coverage is not guaranteed:** public page access/schema can change, and missing, stale or ambiguous evidence still cannot pass. Changing the key alone does not fix subscription coverage.

Optional quote/history fallback: configure `PAPER_ALPACA_KEY` and `PAPER_ALPACA_SECRET` (or `APCA_API_KEY_ID` / `APCA_API_SECRET_KEY`) on the Python server. The GUNS fallback explicitly requires entitled, real-time **SIP**, not free IEX or delayed SIP. IBKR remains first. External quotes are screening-only and never replace the IB execution feed. These keys are not configured through the FMP form.

## 3. Scanner workflow

1. Open **GUNS → 01 / Scanner**. Initial acquisition starts when eligible; **Scan now** requests a manual scan.
2. IBKR discovery and source-status checks run concurrently. Up to 50 discovered stocks first receive zero-network checks from fresh cached data: available traded volume, price and measured spread. Gap waits for dated prior-session evidence; an undated quote close never eliminates a stock. Missing fields remain unverified.
3. Quote acquisition uses six concurrent workers, not one stock at a time. Cached checks are repeated on acquired data. Snapshot reads return as soon as complete fresh bid/ask/trade fields arrive, rather than waiting for IB's roughly 11-second snapshot-end notification. Browser quote waiting and repeated per-symbol retry sleeps have been removed.
4. Survivors get contract/session/history verification: three concurrent jobs, two parallel history requests per job, and 350ms start spacing rather than three seconds. Scan history is one day of minute bars plus one month of daily bars. This checks observed PM volume and the previous RTH close separately from total volume. The pinned adapter's throttling and IB's soft history limits remain.
5. Float is requested last for verified survivors, three at once. Finalists receive concurrent quote refresh; old verification evidence is refreshed if needed before publication.
6. Up to four complete passing snapshots are published. No padding and no fabricated metrics. Cards show price, gap, premarket volume, bid, ask, spread, timestamp and float evidence.

**Speed target: 1–2 minutes, not a hard cutoff.** There is no total-scan or phase deadline. Normal per-request timeouts still handle hung connections. Progress shows complete/total, actual in-flight jobs and elapsed time. A run with acquisition failures and no new complete candidates retains the prior shortlist and offers manual retry instead of automatically restarting indefinitely. No candidate is rejected merely because two minutes elapsed. End-to-end Gateway performance still requires measurement on your connection.

Default thresholds include price ≥$1.50, gap ≥5%, premarket volume ≥30,000 shares, spread ≤$0.05 and float <100M. Consult your current settings; entry checks are repeated independently.

Membership and screening evidence are saved per paper book. Live quotes do not continuously reorder results. Surviving candidates retain their slots on refresh. Automatic scheduling uses the exchange's T−30/open window; manual scan is available otherwise. Recovery attempts are not successful publications. The browser/server/Gateway must be running for scheduled work.

If no cards appear, open **Screening diagnostics / rejections**. Numeric rejection, incomplete acquisition and provider coverage are different problems. A complete scanner pass is not catalyst approval or permission to enter a trade.

## 4. News and human review

- Choose a scanner company or use independent ticker/company search in **02 / News & research**. Research search is not limited to scanner results and does not silently replace the active execution company.
- Open the actual article. IB bodies, short wire flashes, excerpts and PDFs are distinguished. Footer-only responses are not presented as full articles; exact-story source links remain available.
- Recovery must match the selected story; unrelated company news is never substituted. Full bodies depend on source availability/entitlement.
- Check **Favorable catalyst reviewed; NOT fixed-price buyout** only after your own review.
- Inspect the Daily chart and check **Daily overhead resistance / room reviewed** only when you have assessed it.
- Use **Open this company in trading** to assign it explicitly. In Trading, **Review confirmations & optional overrides** contains the same company/day reviews.

The strategy button confirms chart/setup review for calculated entries. When assessments warn it opens your manual paper ticket rather than disabling trading. Reading a headline does not invent market data.

## 5. Charts and controls

Four saved layout presets:

| Preset | Use |
|---|---|
| Four companies / 1m | Compare execution action |
| Four companies / 5m | Compare premarket structure |
| Four companies / Daily | Inspect overhead resistance |
| One company / Execution | Same company across 1m, 5m, Daily and 15m |

Each panel has company search/dropdown, timeframe and reset-view controls. Switch focus or 2×2 view; the active chart determines execution selection. Charts show observed IB bars, volume, EMA9/20 and SMA50/200. Missing indicator warmup is not fabricated. These are local canvas charts, not an embedded TWS window; live parity with TWS remains to be checked.

- **1–5:** arm the calculated entry when ready, or open an explicit manual ticket when assessments warn. The dropdown is not a second transmit step. Shortcuts are blocked while editing fields or in other app tabs/dialogs.
- **H:** toggle hovered-candle entry selection. The actual candle column supplies its high/low, independent of cursor Y-price. Outside the chart → AUTO. Strategy timeframe/session rules still apply; S5 cannot use a later candle.
- **Q:** show/hide quick settings. Risk presets, target R, S1/S2 stop mode and breakeven are also available directly.
- Shortcut bindings can be changed to unique supported Alt+letter/digit combinations.
- Use the strategy summary/expanded tooltip for distance to PM high or pivot, planned entry, remaining cap headroom and applicable formation rules.

Use Daily for resistance, 5m for S1–S3 structure and 1m for execution/S4–S5. Extension is a judgment about distance from the base/support and room to resistance, not a guaranteed indicator signal. Above the stop-limit cap means no chase.

## 6. Strategies, windows and initial stops

Times below are for a normal 09:30 ET regular open; execution uses the reported exchange session. Numeric timing/ATR cutoffs are application guardrails, not claims of an optimal strategy.

| Strategy | Automatic entry reference | Initial stop | App window |
|---|---|---|---|
| S1: Premarket high breakout | Actual PM high +$0.01 | Completed 1m ATR distance by default | Arm from 09:28; fills wait for open; expires 09:35 |
| S2: Premarket pivot | Most recent completed lower 5m pivot +$0.01 | Completed 1m ATR distance by default | 09:28–09:35; fills wait for open |
| S3: Premarket bull flag | Final completed 5m flag candle high +$0.01 | Same candle low −$0.01 | 09:28–09:35; fills wait for open |
| S4: First opening bull flag | Completed opening 1m flag candle high +$0.01 | Same candle low −$0.01 | First hour after open; needs a valid completed reference |
| S5: First bullish minute | First 09:30–09:31 candle high +$0.01 | That first candle low −$0.01 | After first close and strictly before 09:32 |

Prices round to the verified tick. S1/S2 allow explicit PRICE or FIXED stop presets instead of ATR; missing ATR never silently selects another stop. S5 additionally requires a bullish first candle and range ≤2× premarket ATR. S2 requires at least 1R to PM high; other non-S1 strategies apply the app's PM-resistance guard. Human Daily-room review remains separate.

For all strategies, the latest completed basis candle must close above EMA9, EMA20, SMA50 and SMA200: **5m for S1–S3, 1m for S4/S5**, with at least 200 basis bars. A current last trade above an MA is not the same as that completed-candle check. Formation/retracement hints remain advisory; you judge chart quality.

Entry cap: entry +$0.03 when entry is below $20, otherwise +$0.05, tick-rounded. No fills above the cap. A changed pending entry/stop requires review and re-arm. At most two pending/open GUNS trades are allowed in the paper book.

## 7. Risk, automatic breakeven and exits

- Risk budget = **current marked equity × risk %**. At 1%: $90,000 → $900; $100,100 → $1,001.
- Pending quantity is recalculated, including fees, whole shares, buying power and the limit-cap risk. Missing marks on held positions block new entries.
- Targets re-anchor to actual entry fill and chosen R. Changes to risk/R settings do not rewrite existing filled brackets.
- With **Breakeven at +1R** enabled at fill, the stop moves to actual entry when executable bid reaches entry + initial R. It never loosens, but “entry” is **before fees**, so this is not guaranteed net-zero P&L.
- **No continuous ATR trailing stop is implemented.** ATR is used for the initial S1/S2 stop when selected.
- Stops can fill in parts, stay triggered until the remaining quantity exits, and may slip. Targets, manual flatten and the session-close exit also require executable IB quotes.
- **Journal** and **Export research** retain/export browser-local trade lifecycle and screening evidence. Back up your account separately before clearing browser storage.

## 8. S4/S5 Level II protection

Open **S4 / S5 Level II protection**:

- **Monitor Level II** defaults on for pending and open S4/S5 trades in the current paper book. Monitoring continues when another application tab is selected, provided the browser keeps running.
- **Auto-cancel** defaults off. Off means a confirmed red flag holds an unfilled entry for your decision. On cancels an unfilled entry after confirmation. Neither mode undoes a fill or cancels protective exits.
- Defaults flag displayed spread beyond the configured limit (monitoring cap $0.05), aggregate top-five ask size ≥3× bid size, or an ask wall from entry through +0.5R sized ≥4× the median bid level.
- The same flag signature must occur on two distinct depth updates at least one second apart. HTTP polling does not create fresh depth timestamps.
- At least three positive levels per side and depth no older than five seconds are required. Missing/stale/mismatched depth holds new fills. Open-position stops/targets remain independent.
- **Accept current finding / resume (5s)** accepts that same finding/stream for five seconds, subject to every other fill guard. A new finding or expired acceptance can hold the entry again. **Cancel entry** cancels only the pending entry. **Acknowledge (5s)** records an open-position decision; it does not change its stop.

The engine reuses up to three SMART depth streams and expires idle streams after 20 seconds. Displayed depth depends on IBKR entitlements; it is not all market liquidity, does not expose hidden orders and cannot prove spoofing. Shared capacity or missing entitlements can hold entries; the app does not fabricate replacement books.

## 9. Greyed-out buttons: resolve the actual blockers

Trading now shows the selected strategy's full blocker list, and each S button lists its own reasons.

| Blocker | What to check |
|---|---|
| FMP not configured | Save on your local server, not this development sandbox; then retry |
| Float unavailable / HTTP 402 | Symbol coverage, subscription and dated provider response; a saved key alone cannot fix this |
| Gap/volume/price | Current trade, previous RTH close and observed PM bars; genuine threshold failures must remain blocked |
| Catalyst / Daily room review | Read source news and Daily chart, then explicitly confirm your review |
| Quote / spread | Gateway connection, live entitlements, both positive quote sides and measured spread; scanner recovery does not replace fill-feed requirements |
| Chart current / completed minute | Current matching-symbol IB history stream and latest completed minute; reload/reconnect if transport is stale |
| Above MAs | Completed strategy candle and enough history, not cursor price or current last |
| No chase | Ask exceeded the cap; wait for a valid setup rather than bypassing it |
| Window | S1–S3 expire five minutes after open, S4 after first hour, S5 at the second minute |
| Level II waiting/review | Fresh entitled SMART depth; inspect and decide on a confirmed flag |
| Sizing/exposure | Equity, buying power, fresh marks, duplicate symbol and two-trade limit |

## 10. Readiness and next validation

Automated tests use isolated fixtures and the pinned IB wrapper. They can establish request ownership, safety guards, UI behavior and simulated lifecycle behavior; they cannot certify your live Gateway connection or entitlements.

Before relying on a live-data paper session, compare bid/ask, spread, bar times/OHLC/volume, indicator warmup, session schedule, actual news bodies and depth revisions with your read-only Gateway. Test an S4/S5 hold, resume, auto-cancel and protective paper exit with small simulated size. Confirm recovery after disconnect/reconnect and browser sleep.

Outstanding limitations: universal float coverage, live Gateway/Alpaca entitlement validation and TWS chart parity are not established. No continuous ATR trailing, native TWS embedding, cloud collector, broker-held protection or real broker orders were added.
