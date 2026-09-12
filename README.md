# Paper Desk — IB Gateway paper-trading workstation

## Current GUNS workflow — 1.4 (2026-09-12)

This section supersedes older GUNS UI descriptions below; historical implementation notes and course material are preserved. All execution remains browser-local PAPER trading. The single read-only IB session and blocked real-order routes are unchanged.

### What changed and how to use it

1. **Scanner:** discovery is followed by paced contract/history/float checks, then publication of at most four passing candidates. No failed or unresolved discoveries are padded into the main list. Rejections live under Screening diagnostics. The broker scan covers up to 30 US-major-exchange gainers, **not every market security**. Required checks are current LIVE quotes, price, gap, observed premarket volume, common-stock identity, session and spread. News quality, resistance and chart quality remain human decisions.
2. **Stable results:** candidate membership/order is saved per paper book. Quotes and safety checks update without continuously sorting or replacing candidates. A deteriorated or restored candidate keeps its slot and is labeled as awaiting current trading checks. Scan now is explicit; automatic discovery runs initially if no prior attempt exists and once in the actual session's T−30/open window. The first visible GUNS pulse in that window triggers the scheduled attempt; no background/cloud scheduler is implied. The IB SPY liquid-session calendar supplies the schedule; unknown sessions are never guessed.
3. **News:** search another company/ticker directly on News, or use the scanner quicklist. Research selection does not switch an already selected execution symbol. Open research company in active chart explicitly assigns it. Public RSS supplies excerpts and publisher links alongside IB headlines. An unusable IB article triggers attempts to retrieve an alternative article, including a configured full-body source. A different article is explicitly labeled DIFFERENT; it is not represented as the originally requested story. Publisher/legal responses are retained under diagnostics. Reading text never automatically approves the catalyst.
4. **Charts:** Trading offers focus and 2×2 modes with four independent, saved symbol/timeframe slots. Activate a slot, then use company search or the quicklist to assign it. Several slots can show the same company in different timeframes. Only the highlighted ACTIVE EXECUTION chart receives keys 1–5. Wheel zooms; Shift+wheel pans; crosshair/OHLC uses real observed candles. Mobile stacks the panels. EMA9/20 and SMA50/200 remain automatic.
5. **Quick settings:** open Quick settings for risk %, target R, hover mode and automatic timeframe selection when changing the strategy dropdown. Pending entries continue to resize from current marked equity. New risk/R settings apply to previews and pending fills; filled brackets retain their captured stop/target/breakeven settings. Changes to pending entry/stop geometry require explicit re-arm.
6. **S5:** key 5 and its placement button submit the calculated S5 paper entry after required reviews/checks. Existing custom S1–S4 bindings migrate without resetting; conflicts with 5 receive an available Alt binding. All editing/modal/tab/repeat guards remain.
7. **Hover mode:** default off. When on, a valid candle column under the pointer in the active chart supplies its real high, not mouse Y-price. S1/S2 use high + $0.01 and the configured stop method; S3/S4 use that candle's high + $0.01 and low − $0.01. S1's automatic mode still uses the actual PM high. S3 requires completed PM 5m, S4 regular 1m; S1/S2 accept completed PM 1m/5m. S5 still requires the first completed regular minute. Wrong-session, incomplete/forming, daily or mismatched candles block the override rather than bypassing safeguards. Pointer outside the active chart/no candle uses AUTO. Use a placement key while hovering; moving to click a toolbar button means AUTO. Candle/symbol/timeframe metadata is copied into the order and cannot move with the pointer after confirmation.

### News and float setup — important limitations

- Free Yahoo Finance RSS, with Google News RSS as a fallback, supplies **excerpts and links, not guaranteed full stories**. A live public-source probe returned 18 AAPL RSS items; this is availability evidence, not full-body acceptance. Search-derived headlines can be ambiguous: verify the company and catalyst yourself.
- Existing IB API news uses the configured Gateway entitlements. Enable providers in TWS/Gateway API News Configuration. TWS news access and API entitlement are not necessarily equivalent; free DJNL newsletters are not comprehensive breaking-news coverage.
- Optional `PAPER_BENZINGA_KEY` enables the direct Benzinga News API with `displayOutput=full`. It is a separate licensed API credential, not an IB login. This adapter was fixture-tested; no paid subscription was activated or live entitlement verified.
- Optional `PAPER_FMP_KEY` enables Financial Modeling Prep `/stable/shares-float`. The adapter returns the actual `floatShares`, source and date; it never substitutes outstanding shares. Set keys in the **server process environment**, restart Paper Desk, and never put them in HTML, URLs shared with others, source control or chat. No paid purchase was made.
- Float defaults to a below-100M preference. Scanner Strict excludes unknown, stale (45 days or older), or >=100M float. Strict also applies at entry validation. Without a configured source, float is explicitly UNKNOWN, **not checked/passed**. Float responses cache for six hours; external news responses for two minutes. Manual refresh may reuse that provider cache.
- No provider can guarantee text for every headline. If all retrieval attempts fail, original-source/company links remain available; missing text is never invented or concealed as a full article.

### Data architecture, routes and outstanding acceptance

The Python server still runs at **http://localhost:8765**, with no frontend build. New authenticated GET routes: `/data/guns_sources?symbol=...`, `/data/guns_float?symbol=...`, `/data/guns_schedule`. Existing search, bars, scanner verification, articles and SSE routes remain. External sources use fixed hosts, HTTPS, bounded responses and no redirects; credentials stay server-side. Market data and reference-data caches are transient, not a research database. Shortlists and chart slots are saved in the existing browser paper-account storage.

Historical acquisition is serialized on the existing owner loop and capped at six symbol streams (four chart symbols plus two pending-entry symbols). Cached charts bypass cold-acquisition waits. Generation changes and cancelled/failed acquisitions are handled without cancelling a new connection's reused request IDs.

**Not implemented / not claimed:** native TWS GUI embedding (no supported documented interface found), a chart-library replacement, independently verified live TWS candle/ATR parity, universal full-text news, configured paid float/news entitlements, durable cloud journaling or always-on post-exit collection. The enhanced local canvas still plots observed bars by index and labels time gaps rather than manufacturing candles. Feed accuracy and rendering interactivity are separate. Next acceptance step is a read-only live Gateway comparison of contracts, extended-hours/RTH settings, OHLC, indicators and reconnect behavior under the user's entitlements.

### Preservation and verification

Application changes, the final browser fixture and usage documentation are pushed through `c5d68c7`; the following verification update is committed separately. The final browser fixture removes a navigation race by carrying test request timestamps in a test-only header; this does not modify production fetches. Source, tests and the unchanged Adam course are preserved; no credentials, account files or dependencies are committed. If GitHub authorization fails, use the complete Git bundle supplied in chat.

Final verification: 36 JavaScript tests and 37 Python tests passed. The final expanded GUNS browser run reported successful scanner, news recovery, S1–S5 placement, four-slot chart selection, hover capture, quick settings, focus guards, tutorial isolation, dynamic risk, paper lifecycle and mobile checks with no browser errors. The final 500-position browser run also reported no browser errors: positions-tab median 5.75 ms, quote update 8.1 ms, paper acknowledgement 17.1 ms. These are isolated fixture measurements, not live broker latency or Gateway acceptance.

---

Paper trading only. Orders execute in the browser's simulated account, never at IBKR.
The default data connection is now **IB Gateway / TWS socket API**, with continuously
streamed quotes and an automatically reconnecting browser feed.

- Repository: https://github.com/Palakkaalakak/paper-desk (main)
- Local app: http://localhost:8765
- Production URL: none recorded or verified. No production deployment was initiated.
- Stack: Python + ib_async 2.1.0, HTML/CSS/vanilla JavaScript. No frontend build required.
- This is not a Cloudflare/Hono application. Run it on the machine hosting IB Gateway,
  or a trusted Python-capable host that can reach Gateway privately. Do not expose the
  Gateway socket directly to the public internet.

## Start

1. Open IB Gateway and log in. Keep **Read-Only API** enabled in its API settings.
2. In the project folder, install the pinned data adapter once:
   ```sh
   python3 -m pip install -r requirements.txt
   ```
   On Windows use `python` instead of `python3`.
3. Run `python3 serve.py`, or use `start.command` / `start.bat`.
4. Open http://localhost:8765. Search a symbol and trade. Quotes subscribe automatically;
   there is no auto-refresh checkbox to enable.

The first load migrates existing browser settings to the requested IB Gateway mode
without changing cash, positions, fills or portfolios. Previous source selections are
retained in `settings.previousDataSource`. Alternate providers remain in Account settings.

Gateway ports are tried in order **4001, 4002, 7496, 7497**. Broker-side subscriptions,
pacing and connection availability remain authoritative. The simulator does not bypass them.

## Performance results (2026-09-08)

Measured in sandbox Chromium with 500 test positions and mocked market data, not a live
broker. `browser_check.py --baseline` loads original revision `79169d8` for comparison.

| Measurement | Original | Updated |
|---|---:|---:|
| Positions-tab render benchmark, median of 20 runs | 81.3 ms | 5.4 ms |
| Bootstrap data HTTP requests (500 positions) | 502 | 3 |
| Incremental 500-row quote update | not measured | 7.7 ms |
| Paper market-order acknowledgement | not measured | 16.2 ms |

The measured tab benchmark is approximately **15x faster**, and bootstrap request count
is reduced by over 99%. This is not a claim that every operation or live broker response
is 15x faster. A cold 500-contract subscription still needs broker delivery and may take
seconds under IBKR pacing. Warm snapshots are returned without new broker requests.

The page is served gzip-compressed: approximately **190 KB → 59 KB** on this build.
System fonts eliminate a blocking third-party font dependency.

## Completed changes

### Continuous market data

- One dedicated thread owns a continuously running asyncio loop and one ib_async session.
  HTTP handlers never drive the IB event loop. Slow search/contract lookup tasks yield to
  incoming ticks rather than holding a global lock across requests.
- One batched subscription request replaces per-position quote calls. Positions, working
  order legs, watchlists, the selected ticket and visible chain contracts subscribe together.
  Other saved portfolios are also included, within the configured line budget.
- A single authenticated **Server-Sent Events** connection pushes changed quotes to each
  browser. Updates are coalesced at 100 ms; every reconnect gets a full snapshot.
- Automatic Gateway reconnect/resubscription, browser stream recovery, a 20-second
  subscription heartbeat and explicit connected/loading/stale/capacity diagnostics.
- Default local line budget: **500**. Request lists accept at most 2,000 instruments.
  Exceeding the budget is reported, not filled with invented quotes. Set PAPER_TWS_LINES
  to the actual line allowance if different; exchange subscriptions alone do not imply
  unlimited simultaneous lines.
- Idle browser demand expires after 60 seconds. Unused broker subscriptions are released.
  Bounded lookup caches and in-flight request sharing avoid redundant contract lookups.
- Option definitions load separately from prices. Quotes/greeks stream after visible
  strikes are selected. Expiry requests are guarded against stale asynchronous responses;
  switching expiries can reuse cached definitions instead of waiting for all prices.

### Faster execution and UI

- Cached number formatters, cached structural HTML, incremental position-cell updates
  and frame-coalesced rendering instead of repeatedly replacing the entire page.
- Order lookup is indexed by contract for tick processing rather than scanning full
  historical order lists on every quote.
- Stable quantity/price inputs while quotes update, preserved focus/scroll during structural
  changes, a dense dark workstation, sticky position headers and responsive mobile layout.
- Quantity presets and Bid/Mid/Ask limit-price shortcuts. Optional **Alt+B / Alt+S** paper
  order shortcuts, enabled explicitly on the ticket. `/` focuses symbol search.
- Duplicate double-click/repeated-key protection; explicit working/filled/rejected notices.
- Gateway fills require a fresh (under 15 seconds), live, non-halted, two-sided quote and
  a healthy stream. Disconnected, stale, frozen and delayed Gateway prices cannot fill orders.
  Quiet/closed markets can therefore leave orders waiting; no fake live status is used.
- Positive finite quantities are required; derivatives use whole contracts. Execution-price
  buying power is rechecked, including before atomic combo fills. Risk-reducing actions are
  allowed where the account is otherwise deficient.
- Saves are scheduled after 150 ms without endless debounce postponement. Page hide/unload
  flush pending changes. Account export now works locally without an artifact runtime and
  excludes provider keys.
- Missing underlying quotes no longer erase expired options as worthless. Expired options
  stay visible with **Settle expiry**, requiring an explicit official per-option-unit
  settlement value and confirmation. This is manual paper cash settlement, not physical
  exercise/assignment. Nothing silently substitutes a current underlying quote.

## Architecture and files

| File | Responsibility |
|---|---|
| `paper_local.html` | UI, quote adapters, SSE client, simulation, margin, portfolios, local persistence |
| `serve.py` | HTTP server, compressed page, authenticated data routes/SSE and restricted Client Portal proxy |
| `market.py` | Production IB Gateway owner loop, quote snapshots/deltas, subscriptions, search, chain definitions, depth |
| `providers.py` | Alternate provider adapters and diagnostics; TWS requests delegate to market.ENGINE |
| `tws.py` | Existing standalone diagnostic adapter, shared seal/cache utilities; not the app's streaming loop |
| `check.py` | Live read-only diagnostic using client ID 78, separately from app client ID 77 |
| `ws.py` | Legacy Client Portal WebSocket codec; not used by the new IB Gateway SSE path |
| `test_security.py` | 16 HTTP/security/adapter regressions |
| `test_market.py` | 9 offline streaming, capacity, lifecycle and SSE-wire regressions |
| `test_frontend.cjs` | 3 inline JavaScript/gateway expiry regressions |
| `browser_check.py` | Chromium scale and interactive trading/mobile/persistence checks using test-only fixtures |
| `checkpoint.cjs` | Development-only automatic GitHub recovery snapshots every 30 seconds |
| `browser_check.py --screenshot` | Generates an optional test-only screenshot; not required at runtime |
| `START_HERE.md`, `DEPLOY.md` | Additional setup and hosting guidance |

The `market.py` path replaces the old synchronous TWS request/pump path for the app.
Do not reintroduce calls to `tws.connect()` from HTTP workers: that creates a second
session and revives thread/event-loop contention. `check.py` remains a separate diagnostic.

## Data and persistence

`S` contains account/settings, cash, positions, orders, trades, cashflows, equity, watchlist,
log, fees, builder state and multiple portfolios (`books`/`bookId`). `Q` is transient market
data. The active portfolio runs the order engine; other books can have warm quotes but
working orders are not processed while those books are inactive.

The full paper account is stored as `localStorage['paperAccount']`, per browser and origin.
GitHub does **not** back up this account. Export it privately before clearing browser data
or changing devices/domains. Provider keys entered in settings remain in localStorage;
exports remove them, and hosted installations should prefer server environment credentials.

The Gateway engine keeps transient quote/lookup caches in memory. Regenerable contract
metadata is stored in ignored `.contracts.json`. There is no server account database,
cloud portfolio synchronization or background order execution after closing the browser.

## API entry points

| Route | Purpose |
|---|---|
| `GET /`, `/index.html` | Workstation. `?t=TOKEN` signs in if access protection is set |
| `GET /manifest.json`, `/icon.png`, `/favicon.ico` | Public install assets without private data |
| `POST /data/subscriptions` | JSON `{client, instruments:[{conid,symbol,secType,exch,...}]}`; nonblocking subscription update + warm snapshot |
| `GET /data/stream?client=...` | Authenticated SSE `quotes` events containing status, sequence and quote deltas |
| `GET /data/providers` | Provider capabilities |
| `GET /data/search?provider=tws&q=...` | IB contract search with real conIds |
| `GET /data/chain?provider=tws&symbol=...&expiry=YYYYMMDD` | Expiry metadata, or contract definitions for one expiry |
| `GET /data/quote`, `/data/selftest` | Provider/diagnostic reads; provider, symbol, optional key |
| `GET /data/twsstatus?client=...` | Engine status/snapshot; no separate TWS connection |
| `GET /data/depth?symbol=...` | Gateway depth request |
| `/api/*`, `/ws` | Legacy Client Portal adapter, only when explicitly selected |

Client Portal POST allowlist: auth status, reauthentication, secdef search and account
orders/whatif. Actual order placement, modification, confirmation and cancellation routes
are blocked. Client Portal what-if controls are hidden in IB Gateway mode because they
require the separate Client Portal gateway.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `PAPER_PORT` | 8765 | App port; host `PORT` takes precedence |
| `PAPER_BIND` | 127.0.0.1 | Listen address; external access requires explicit configuration |
| `PAPER_NO_BROWSER` | unset | 1 prevents opening a local browser |
| `PAPER_TWS_HOST` | 127.0.0.1 | IB Gateway/TWS host |
| `PAPER_TWS_PORTS` | 4001,4002,7496,7497 | Connection preference |
| `PAPER_TWS_CLIENT_ID` | 77 | App session client ID |
| `PAPER_TWS_LINES` | 500 | Local concurrent subscription budget; match actual IBKR allowance |
| `PAPER_TWS_DATA` | live | Set delayed only for diagnosis; delayed quotes cannot execute Gateway paper fills |
| `PAPER_ACCESS_TOKEN` | unset | Strong URL-safe token required for public exposure |
| `PAPER_PROVIDER_KEY` | unset | Server-side credentials for alternate providers |
| `PAPER_GATEWAY` | https://localhost:5000/v1/api | Separate Client Portal API base |
| `PAPER_GATEWAY_WS` | derived from gateway | Legacy Client Portal WebSocket URL |

For browser streaming behind a proxy, disable SSE buffering, allow idle connections
beyond the 10-second heartbeat, and preserve Host and X-Forwarded-Proto. Use HTTPS.
Authentication covers GET, POST, DELETE and stream upgrades; cookies are exact-match,
HttpOnly, SameSite=Lax, and Secure when served through an HTTPS proxy. Body sizes and
subscription counts are bounded. Cross-origin browser requests are rejected.

Gateway order methods are sealed on **both** the IB wrapper and its low-level client,
in addition to `readonly=True`. Keep IB Gateway's own **Read-Only API** enabled too.
This is a personal application sharing one broker-data session, not multi-user brokerage
infrastructure. Legacy Client Portal TLS accepts its local self-signed certificate;
do not point that proxy at an untrusted remote service. Redact token/key query strings
in host/proxy logs as well as application logs.

## Tests and acceptance status

```sh
python3 -m unittest -v
node --test test_frontend.cjs
# Development-only browser tools:
python3 -m pip install playwright
python3 -m playwright install chromium
python3 browser_check.py
python3 browser_check.py --baseline
```

Validated: **25 Python + 3 Node tests**, plus Chromium checks for 500 positions, continuous
quote application, stable inputs, market and tick-filled limit orders, stale/delayed/
disconnected protection, browser persistence/reload, manual settlement, export and mobile
layout. Streaming tests exercise real local HTTP/SSE with a fake broker and verify one
owner thread, no warm re-subscriptions, reconnect rebuilding and explicit line capacity.

**Not verified:** a live IB Gateway session, real exchange entitlements/pacing, broker
response-shape compatibility under every condition, or unattended production operation.
No real broker connection is available in this sandbox. Test fixtures never ship as app
quotes. Final acceptance must be performed on the user's Gateway machine before relying
on the simulator for demanding practice sessions.

Financial models remain simulations: Reg-T-style margin rather than portfolio margin/SPAN,
liquidity-class queue/book-walking estimates, manual expiry cash settlement instead of
physical exercise/early assignment, and no server-side fills while the browser is closed.
The UI warns on margin deficiency instead of automatically liquidating. Fee and futures
margin defaults are editable and must be checked against current schedules.

## Recovery / continuation

**User requirement:** preserve all non-sensitive source, tests and reference notes in
GitHub, not just local commits. Work on `main`; push tested checkpoints without force.

While editing, `pm2 start checkpoint.cjs --name paper-recovery` snapshots changes and
pushes **recovery/performance-work every 30 seconds**. It uses a separate Git index and
commit-tree, so it does not switch branches, alter the working index, or deploy `main`.
New non-ignored source/test files are included automatically; secrets, environments,
cache/dependency folders and runtime files remain excluded. Never put credentials in source.
Stop with `pm2 delete paper-recovery` when work ends. It must be restarted after a sandbox
reset; it is a development process, not part of the production app.

To resume after a wipe, fetch origin and inspect the recovery branch before restoring
its changed files. Recovery snapshots may be incomplete between edits: always run the tests
before committing them to main. Previous long-form adapter/model notes remain available
in revision `79169d8`; this README describes the current streaming architecture.

## GUNS upgrade: user-confirmed paper workstation (2026-09-11)

Status: GUNS tab, user-confirmed S1–S4 buttons/shortcuts, independent scanner evidence,
licensed news article reader, four-stock guided tutorial, dynamic sizing and browser-local
paper bracket management are implemented and offline-tested. Cloud/post-exit collection
is NOT implemented; live Gateway acceptance has not been performed.
See the current implementation section below; the original design remains as a roadmap.

### Historical baseline review
The user requested inspection of intervening changes before further edits. A fresh fetch
found local main and origin/main at `ba883b7` with a clean working tree. The previous
session's local option-chain commits `1067e0d` / `d8d3adb` are absent from this reset
checkout and fetched branches. Do not claim those changes are deployed or recovered.
GitHub authorization succeeded during this review; preserve incremental commits remotely.

### Authoritative source

`GUNS_MASTER_DOCUMENT.md` is the complete, unchanged user upload (153,426 bytes).
SHA-256: `90cf9f33ed648b936809629dd1f1a92c019100bf5a2c0279c63b3426053e7a34`.
It documents Gap Up News Scalp, five long stock setups, preparation, execution and Level 2.
It is reference material, not evidence that projected returns or trade examples are reliable.
The whole document, including long paragraphs truncated by the file viewer, was read.

### Original workspace roadmap (not a completed-feature list)

- A first-class GUNS tab with Preparation, Execution and Review views. Linked daily,
  five-minute and large one-minute charts; premarket shading; 9/20 EMA and 50/200 SMA;
  configurable ATR period (the course does not specify one), premarket high, pivot,
  trigger candle, entry, stop, target and 1R levels. No fabricated historical candles.
- Broker scanner candidates: corporate common stocks, price at least $1.50, gap at least
  5%, current premarket volume at least 30,000; ranked by volume. Instrument classification
  must be verified rather than assuming every STK is a corporate common stock.
  Catalyst/news source and float provenance stay visible; unknown data remains unknown.
  Pin four to six candidates and default to monitoring two execution candidates.
- Explicit setup selection/checklists: S1 premarket-high breakout; S2 lower-pivot breakout
  with at least 1R clearance; S3 premarket bull flag; S4 first post-open bull flag; S5 first
  completed bullish one-minute candle only. Qualitative pattern judgements are confirmed by
  the trader, not represented as perfect automatic detection. Use America/New_York and
  actual exchange sessions, not the browser timezone or a fixed UTC offset.
- Risk-sized PAPER stop-limit brackets, tick-aware rounding, 2R/2.5R choices, planned risk
  versus worst permitted entry risk, buying-power limits, entry/exit spread and freshness
  gates. Stops/targets must be linked OCO and sized to actual partial fills; manual exits
  must reduce/cancel child orders without opening an unintended short. Breakeven at +1R
  is an explicit management setting, not a claim of guaranteed zero loss after fees/gaps.
- Live depth is supplemental evidence, never a guaranteed predictor. Use broker/API units,
  not a hard-coded multiplication by 100 based on the course's TWS display convention.
  Provide tight-spread and nearby ask-wall warnings and a paper flatten control.

### Strategy-neutral research and post-exit tracking

- Append-only, idempotent execution events, strategy/version/setup tags, immutable entry
  plan and market context, partial fills, exit reasons, commissions and execution quality.
  Distinguish position episodes, lots, portfolio IDs and reversals; do not confuse fills
  in S.trades with complete round trips or relabel old untagged fills as GUNS.
- Continue recording after exit at proposed configurable horizons of 1/5/15/30/60 minutes,
  session close and 1/3/5 subsequent exchange sessions. Track sampled paths and coverage,
  actual timestamps, benchmark type (bid/ask/last/bar), source and data quality.
- Review actual net R, expectancy, win rate with sample counts, holding time, favorable/
  adverse excursion during the trade and after exit, and costs. Compare by strategy/setup,
  catalyst, spread, time-of-day and rule adherence. CSV/JSON export supports later research.
- Counterfactuals (hold longer, 2R vs 2.5R, breakeven vs original stop) are separate from
  realized results. OHLC bars cannot establish stop/target ordering when both are touched;
  flag ambiguous paths. Missing observations and outages must never become zero returns.
  Historical backfills must be labeled and cannot recreate missing Level 2/tick sequences.
- Long-horizon jobs must persist independently of browser tabs. Proposed durable storage:
  Cloudflare D1 accessed server-side by the Python service, with optional R2 for larger
  datasets. This needs an approved storage/hosting path and securely configured credentials;
  no credential belongs in browser code or Git. Do not use sandbox/localStorage-only data
  as a purported durable research archive.
- The existing Python/Gateway machine (or another authorized collector host) must stay on
  for continuous collection. D1 storage alone does not run an IB socket collector. On
  downtime, record gaps and request paced broker historical backfill where available.
  Research writes must not block quote handling or paper execution. If archival writes
  fail, visibly report unsynced/failed state rather than silently dropping events.

### Source conflicts to handle explicitly

- Core premarket-volume filter is 30,000; examples later use 150,000/200,000. Offer named,
  configurable stricter presets, not an unexplained replacement of the core threshold.
- Spread guidance varies between a strict five cents for S4 and a broader ten cents.
  Default conservatively and record the actual threshold/version used on each trade.
- Hot-button examples sometimes anchor brackets to the clicked chart price; later setup
  rules anchor them to entry. Label the anchor and compute real arithmetic from selected
  prices; do not claim identical risk under different fills or a maximum guaranteed loss.
- Some example arithmetic, Singapore/Eastern times and the EXEL stop-out/win narrative are
  inconsistent. Preserve the reference verbatim but do not seed these as verified trades,
  current regulations, profit promises or financial ground truth.

### Implementation entry points and prerequisites

At the original design checkpoint, market.py had no historical bars/scanner/news
collector, and applyFill() had no bracket lifecycle. Both GUNS adapters and the local
bracket controller have since been added; depth remains a transient read-only request.
The engine drops browser demand after 60 seconds. No server journal/database exists.
Add isolated read-only broker data adapters and durable research ingestion/collector;
keep tws._seal(), readonly=True and the real-order proxy deny rules unchanged.
Focused tests should cover strategy math, session/DST rules, stale data, partial/OCO fills,
retry idempotency, post-exit horizons, missing/ambiguous observations, and UI integration.
Before CLOUD implementation, confirm durable storage/hosting and collector availability
with the user. Do not restart IB Gateway, overwrite account state, or continue unrelated chain work.

### Current implemented features and user guide

1. Open the existing app at http://localhost:8765, select IB Gateway as the data source,
   then open the **GUNS** tab. No frontend build or migration is required.
2. Scan preliminary US gainers or search for a stock. The selected name has 1-minute,
   5-minute and daily candlestick charts, EMA 9/20, SMA 50/200, news and displayed depth.
   Charts show received broker data only. Scanner results are candidates, not approvals.
3. Choose S1–S5 and configure percentage risk, 2R/2.5R, ATR/price/fixed stop settings,
   spread and volume. Review common-stock classification, favorable catalyst (excluding
   fixed-price buyouts) and daily resistance. These qualitative judgments remain human.
4. The displayed budget is always current marked equity times risk percentage:
   100,000 at 1% = 1,000; 90,000 = 900; 100,100 = 1,001. Pending quantities are recomputed
   and checked again before fill. Whole shares, estimated round-trip fees and buying power
   may leave risk budget unused; the UI shows this. All held positions need fresh marks.
5. Judge chart quality yourself, then confirm using **S1–S4** buttons or default **1–4**
   keys. The main confirmation button also supports the selected S5. There is no auto-arm:
   every new entry needs your confirmation. At most two pending/open GUNS trades are allowed.
   Entry windows follow the exchange schedule; missing/stale charts, quotes or sessions
   block entries. Changed pending trigger/stop prices require review and re-arming.
6. Protection covers actual filled quantity. The unfilled entry remainder is cancelled.
   Stops latch, partial exits cannot exceed held/displayed quantity, targets re-anchor to
   actual entry, optional breakeven activates at +1R, and session-close/manual flatten
   waits for a valid live quote. Manual reducing sells adjust protection. Same-symbol
   increases, oversells and conflicting combos are rejected. Portfolio switching, deletion,
   reset and data-source changes are blocked while GUNS exposure needs management.
7. Closed trades appear in the journal with fees, net P/L, actual R, sampled bid extrema
   and lifecycle events. **Export research** downloads JSON for all saved GUNS books.

### Confirmation controls, scanner evidence, news and tutorial

- **Your responsibility:** review the actual chart/setup, catalyst, instrument and overhead
  resistance; choose risk/stop settings; confirm the specific setup. Pattern hints are
  advisory, not chart approval. Optional reviewed trigger-high overrides are per symbol,
  day and setup for S2–S4 only. S1 always uses the actual observed premarket high and ignores
  any saved legacy override. S3/S4 overrides require the corresponding candle low; missing lows never
  silently fall back to ATR. Numerical rules (including moving averages, entry windows,
  valid prices, freshness, buying power and no-chase) remain enforced.
- **Automatic after confirmation:** pending risk resizing, trigger/cap checks, paper entry,
  actual-fill-sized protection, configured breakeven/target/stop management and journaling.
  Entry review and nested level/news references are copied immutably into the order.
- **Shortcuts:** expand Entry keyboard shortcuts, focus a field and press a unique 1–4 or
  Alt+letter/digit chord. Settings are stored in `S.guns.config.shortcuts`. Browser-reserved
  Alt chords may not work. Keys are ignored outside GUNS, in a hidden document, while
  editing, on focused buttons, during repeated/composing events, and in dialogs. Shortcut
  input focus and the expanded settings panel survive account-save rerenders.
- **Scanner:** IB scanner results are preliminary contracts, not verified quotes/volume.
  The selected stock and first six broker-ranked rows receive separate, serially paced
  contract/history verification (shared server lock; at least three seconds between starts).
  Other rows remain explicitly unverified until selected. Evidence is cached 60 seconds,
  retried roughly every 65 seconds, and rejected for ranking after 90 seconds. Live quote
  freshness is checked separately. No claim of exhaustive market coverage is made.
- **Attention score, not expected profit:** eligible candidates are sorted by
  `min(40,10*log10(max(1,PM volume/30000))) + min(30,gap%) +
  30*max(0,1-spread/maxSpread)`, rounded, then broker rank. Eligibility requires fresh
  LIVE quotes, matching conId, COMMON classification, known session, price >= $1.50,
  gap >= 5%, configured observed PM volume and spread. Unknown evidence does not pass.
  The source/coverage panel shows prior completed RTH close/date, verification time and
  observed premarket bars. Missing minutes may mean inactivity or missing data, not zero
  volume or proof of complete tape coverage. API volume is used without a display-lot multiplier.
- **News:** headlines refresh approximately every 30 seconds. Provider names, article IDs,
  source time and entitlement warnings are shown. Read article requests licensed API text;
  publisher HTML becomes escaped display-only text. Binary/PDF articles require a licensed
  terminal. Empty results do not establish absence of news. Opening an article records a
  reference but never marks the catalyst favorable. Your Gateway username needs the relevant
  API news entitlement; availability in TWS alone is not sufficient.
- **Guided tutorial:** click Guided tutorial with no pending/active GUNS exposure. Gateway
  is not required. ZORA-DEMO (S1 target), NIMB-DEMO (S2 stop/slippage), LUMA-DEMO (S3 no-chase)
  and VELA-DEMO (S4 partial fill/breakeven) guide discovery, news, chart acceptance/rejection,
  exact equity-risk examples, confirmation, protection and review. Prices/news are fictional;
  OHLC candlesticks show authored fictional bars with automatically calculated entry/SL/TP overlays. Prices advance
  only by explicit actions. Tutorial-local keys never reach account execution. The model has
  no account, network or localStorage bridge and does not send fake instruments to Gateway.
  Existing unrelated paper-account processing is not suspended by training; use an idle
  account for an interruption-free tutorial. These scripted outcomes are not backtests.

New authenticated URIs:
- `GET /data/guns_scan?provider=tws`: preliminary scanner candidates.
- `GET /data/guns_bars?provider=tws&symbol=SYMBOL`: streamed minute/daily history snapshot.
- `GET /data/guns_news?provider=tws&symbol=SYMBOL`: entitled headlines and provider metadata.
- `GET /data/guns_verify?provider=tws&conid=ID`: independent contract and historical evidence.
- `GET /data/guns_article?provider=tws&newsProvider=CODE&articleId=ID`: licensed display-only text.
- `GET /assets/guns.js`, `guns-execution.js`, `guns-workflow.js`, `guns-tutorial.js`,
  `guns-ui.js`, `guns.css` under `/assets/`:
  explicit allowlisted assets. Existing quote subscriptions/SSE/search/depth are reused.

Data remains in the existing browser account `paperAccount`: `S.guns.config` and
`S.guns.books[bookId]` contain review notes, active brackets and closed-trade journal.
Historical chart subscriptions are transient IB Gateway streams. No Cloudflare database,
cloud collector, durable server journal or post-exit job has been provisioned.
**Keep browser, Python service and Gateway running.** Broker access remains sealed and
read-only. No broker orders are sent. Stops do not guarantee a maximum loss; browser
suspension, disconnections, gaps and slippage can delay exits or exceed planned risk.

Known limits / recommended next work:
- Live Gateway acceptance remains to be performed with the user's connected instance.
- Flag/pivot detection is heuristic. Premarket volume is conservatively required even
  for S4; ATR period 14 and S5 2x-ATR range checks are explicit app defaults/guardrails.
- Pending plans revalidate but do not automatically move to newer candle triggers.
- Chart pan/zoom, multi-candidate pinning, float verification and advanced statistics are
  roadmap items. Minimum history is required for SMA 200; thin names can remain blocked.
- Strategy-neutral durable research and minutes/days post-exit tracking are still next.
  An always-on host/data connection independent of the user's PC is necessary; storing
  rows in D1 alone cannot run a Gateway socket collector.
- Use one execution tab per account. Browser-local storage is not a multi-writer ledger.

Validation performed on this implementation:
- `node --test test_guns.cjs test_frontend.cjs`: 29 tests passed, including user review,
  immutable notes, missing flag lows, shortcut validation, ranking and all tutorial outcomes.
- `python3 -m unittest -q`: 31 tests passed, including authenticated assets/routes,
  conId identity, previous close/completed PM bars, DST, unknown volume and safe news text.
- `python3 browser_check.py`: 500-position, order, freshness, persistence, export and
  mobile checks passed with no browser errors.
- `python3 browser_guns_check.py`: passed independent scanner evidence, escaped article
  rendering, S1–S4 controls, editable/focused-button/repeat key guards, alternative binding
  persistence, duplicate binding rejection, reviewed levels, equity re-sizing, partial fills,
  breakeven, latched exit and JSON export. All four tutorial lessons passed with unchanged
  serialized account and localStorage; 390px mobile layout passed. No browser errors.
- All market data in these tests are isolated fixtures, not live broker acceptance.

Preservation: temporary GitHub authorization failures were covered by uploaded full-history
Git bundles. Authorization was subsequently restored and recovered implementation plus
focused tests were pushed to `main`, through `4558b0a` before this documentation update.
The earlier uncommitted edits were lost on resets and have now been restored/tested/pushed.
Always commit and push small checkpoints; if authorization fails, upload a complete bundle
promptly. Preserve references/source/tests, never credentials, dependencies or account data.
`GUNS_HANDOFF.txt` preserves the uploaded historical handoff and is superseded by this
section for current implementation status. No production deployment was performed.


## Current staged GUNS finish (2026-09-11)

This section supersedes older single-screen instructions above.

- **Scanner**: broad candidate grid; selected symbols advance to News & research.
- **News & research**: filterable headlines alongside a large article reader. Review
  common-stock/catalyst/resistance confirmations here before Trading.
- **Trading**: one large chart with 1 MIN / 5 MIN / DAILY controls; automatic EMA9/20,
  SMA50/200. Numbered buttons **1–4 place that selected strategy**, not merely switch the
  dropdown. Entry, SL, TP and whole-share sizing are calculated and previewed on each
  button. Risk settings, overrides and diagnostic checks are expandable. Shortcuts only
  operate in this Trading stage. Browser paper protection continues in other stages.
- **Formation & rules**: reviewed summaries of Adam's supplied course sections for all
  five setups, including formation, entry, SL, TP, invalidation and application deviations.
  S1 is prioritized: observed premarket high + **$0.01**, never a pivot or manual substitute.
  S2 uses the lower pivot; S3 the final premarket flag candle; S4 the opening flag candle.
  Offsets are one full cent, rounded to the valid tick—not merely one subpenny tick.
- **Journal**: paper round trips and export, away from the trading chart.
- **Tutorial**: four isolated fictional OHLC candlestick lessons, using the same numerical
  placement function as the desk. Fictional data never enters Gateway subscriptions or
  account execution. S1/S2 tutorial stop distances are explicitly authored presets; the
  working desk defaults to one-minute ATR, with period 14 an app choice.

### Real data, chart interface and latency

The working desk consumes IB Gateway TRADES OHLC data and renders it locally. It does not
embed TWS's native chart window, screen-scrape IBKR charts, use another vendor's price feed,
interpolate missing candles or substitute midpoint values as GUNS last trades.
IBKR's documented socket API is a data/message interface; Advanced Charts are documented
as a TWS application feature, not an embeddable chart-window API:

- https://www.interactivebrokers.com/docs/tws-api/doc/introduction
- https://www.interactivebrokers.com/campus/trading-lessons/tradingview-advanced-charts-in-tws/

For IBKR's exact native chart interface, use TWS alongside Paper Desk, keeping all broker
order access read-only. A native-chart embedding has not been implemented or claimed.

The app displays Gateway quote receipt age and history-update age, **not measured exchange
to-browser latency**. Quote delivery/processing remains on the existing SSE/tick path.
History snapshot polling is approximately once per second; server snapshot cache is 250ms,
down from the prior 1-second cache plus roughly 3-second browser poll. These settings do
not speed up the broker's underlying bar publication. Zero latency is not promised.

Entry guards now require valid ordered OHLC, the current latest completed minute, history
update age under 15 seconds, and (S2/S3) the latest complete premarket five-minute candle.
Missing one-minute observations cause a five-minute group to be marked incomplete rather
than manufactured. Incomplete groups are excluded from automatic setup calculations and
studies; the chart labels genuine partial/forming bars and gaps. S1's observed premarket
high includes the current real forming premarket bar, rather than waiting for its close.
Unknown volume stays unknown. S4 retains the stricter $0.05 maximum spread. These guards
can intentionally block thin/stale names. Missing coverage can reflect no trades or missing
data; the app cannot prove a complete tape. Live Gateway acceptance is still outstanding.

### Incomplete news handling

The reported `(END) Dow Jones Newswires ... Copyright ... statements ...` response is
recognized as footer/legal material, not presented as a usable story. Reader warnings state
when Gateway returns only a short fragment/footer. Legal notices and the full normalized
returned text remain inspectable; they are not deleted. No missing story is invented.
HTML is converted to escaped display-only text; excessive blank lines are normalized.
`contentStatus` reports `body_returned`, `incomplete` or `binary`; body presence is only a
heuristic, not proof of publisher completeness or accuracy. Only usable returned text is
recorded as opened news evidence, and reading never approves the catalyst automatically.
The reader offers a clearly labeled external search for the original release, not a
purported verified publisher URL. News availability still depends on the API entitlement.

Final offline validation: **29 JavaScript tests, 31 Python tests, staged GUNS browser flow
and 500-position browser regression pass**. Browser checks cover all five mobile stages,
large timeframe-switchable charts, candle tutorial/account isolation, article escaping,
strategy controls and paper lifecycle. The footer-only response is tested separately.
No production deployment or real broker trading was enabled. Source, course reference,
tests and continuation information are preserved in GitHub main. Working account data,
credentials, dependencies and caches remain excluded.
