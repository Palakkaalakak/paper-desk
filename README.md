# Paper Desk — IB Gateway paper-trading workstation

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

## GUNS upgrade: implemented paper workstation (2026-09-10)

Status: GUNS tab, read-only data endpoints, dynamic sizing and browser-local paper
bracket management are implemented. Cloud/post-exit collection is NOT implemented.
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
5. Arm a PAPER stop-limit entry, or opt into one-shot auto-arm for the selected setup.
   Auto-arm is not restored across reloads. At most two pending/open GUNS trades are allowed.
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

New authenticated URIs:
- `GET /data/guns_scan?provider=tws`: preliminary scanner candidates.
- `GET /data/guns_bars?provider=tws&symbol=SYMBOL`: streamed minute/daily history snapshot.
- `GET /data/guns_news?provider=tws&symbol=SYMBOL`: entitled news headlines.
- `GET /assets/guns.js`, `guns-execution.js`, `guns-ui.js`, `guns.css` under `/assets/`:
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
- `node --test test_guns.cjs test_frontend.cjs`: 17 tests passed.
- `python3 -m unittest -q`: 26 tests passed, including asset/data authentication.
- `python3 browser_check.py`: 500-position, order, freshness, persistence, export and
  mobile checks passed with no browser errors.
- An isolated GUNS browser fixture also passed scanner, charts, review checks, pending
  equity re-sizing, actual partial fill, breakeven, latched partial exit, JSON export,
  local persistence and mobile layout. This is not a live broker acceptance test.

Preservation: GitHub authorization was unavailable during implementation. Incremental
full-history Git bundles were uploaded; local-only commits are not considered durable.
`GUNS_HANDOFF.txt` preserves the uploaded historical handoff and is superseded by this
section for current implementation status. No production deployment was performed.
