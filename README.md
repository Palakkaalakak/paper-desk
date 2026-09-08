# Paper Desk — standalone paper-trading simulator

A single-file browser application with a Python standard-library server. It uses
IBKR Client Portal, local TWS/IB Gateway, or fallback market-data providers. The
simulated account lives in browser storage, not at a brokerage or in a server database.

- **Repository:** https://github.com/Palakkaalakak/paper-desk
- **Local URL:** http://localhost:8765
- **Production URL:** not recorded or verified in this repository.
- **Deployment:** existing Render/Docker configuration; no new deployment was initiated
  during the 2026-09-08 review. A GitHub push may trigger an independently configured host.
- **Stack:** Python + HTML/CSS/vanilla JavaScript. There is no Node build step.
  This is not a Hono/Cloudflare application; Workers cannot run its Python process,
  local TWS socket, or filesystem-backed contract cache as-is.

## Quick start

```sh
python3 serve.py
```

Open http://localhost:8765 and leave the terminal running. macOS/Linux also have
`start.command` / `start.sh`; Windows has `start.bat`. See [START_HERE.md](START_HERE.md)
for platform-specific steps. Do not double-click `paper_local.html`: use the server
so the page can access its same-origin data endpoints.

The base application needs only Python's standard library. TWS support is optional:

```sh
python3 -m pip install ib_async
```

For free data, use **Account → Market data source → Free data only**, then choose a
provider. Quotes and option chains can use different providers. For Client Portal:

1. Download the gateway from https://www.interactivebrokers.com/en/trading/ib-api.php.
2. Start `bin/run.sh root/conf.yaml` (Windows: `bin\run.bat root\conf.yaml`).
3. Open https://localhost:5000 and log in. Its local self-signed certificate produces
   an expected browser warning.
4. Start Paper Desk and leave its source on gateway/automatic fallback.

Use the Account tab to configure portfolios, sources, fees, and simulation settings;
Trade for tickets and option strategies; Positions and Orders for the book; History
for activity and exports. The diagnostics line below the title reports source failures.

## Architecture and files

| File | Responsibility |
|---|---|
| `paper_local.html` | Entire frontend: gateway/free adapters, streaming, pricing/greeks, simulated fills, margin, portfolio state, UI and persistence |
| `serve.py` | Threaded HTTP server; page/PWA assets; restricted Client Portal REST proxy; WebSocket bridge; provider endpoints and access-token handling |
| `providers.py` | Alpaca, Tradier, Finnhub, Twelve Data, Yahoo, Nasdaq, CNBC, Stooq; normalization, fallback and response caching |
| `tws.py` | Optional `ib_async` connection, subscriptions, contract resolution/cache, chain loading, delayed feeds, depth and diagnostics |
| `ws.py` | Minimal RFC 6455 socket codec for the bridge |
| `check.py` | **Live**, read-only TWS diagnostic; not an offline test suite |
| `test_security.py` | Standard-library offline HTTP/security and adapter regressions |
| `test_frontend.cjs` | Node built-in tests for inline script syntax and gateway expiry handling |
| `start.command`, `start.sh`, `start.bat` | Local launchers; Unix scripts are executable in Git |
| `Dockerfile`, `Procfile`, `render.yaml` | Existing Python-host deployment configuration |
| `START_HERE.md`, `DEPLOY.md` | Usage and hosting reference |

Keep the HTML and Python modules together. Runtime flow:

```text
Browser UI / paper engine
  ├─ /api/* → serve.py → Client Portal REST gateway
  ├─ /ws → serve.py + ws.py → Client Portal WebSocket
  └─ /data/* → serve.py → providers.py → provider HTTPS APIs
                                      └─ tws.py → local TWS/IB Gateway socket
```

### Data models and persistence

- `S` in the frontend is the account/settings state. `Q` is transient quote state.
- `S` includes `account`, `settings`, `cash`, `realized`, `positions`, `orders`,
  `trades`, `cashflows`, `equity`, `watchlist`, `log`, strategy-builder state and fees.
- `S.books` stores separate portfolios; `S.bookId` identifies the active one.
  `bookSync()` snapshots the active book before `commit()` writes the complete state.
  Machine-level settings and provider keys remain shared across portfolios.
- `localStorage['paperAccount']` holds the saved state **per browser and origin**.
  Clearing site data deletes it; changing ports/domains or devices does not migrate it.
  GitHub preserves source code, not users' browser accounts. Keep separate exports.
- Provider keys entered in the UI are currently saved in that browser state. For a
  hosted installation prefer the server's `PAPER_PROVIDER_KEY` environment variable.
  Never commit credentials or account exports containing credentials.
- `providers._CACHE` is transient server memory. Quote/search/chain request cache keys
  now distinguish credentials using a SHA-256 fingerprint, not just key presence.
  Network quotes cache for 15 seconds, searches 300 seconds and chains 90 seconds;
  TWS quotes/chains use shorter 0.5/2-second TTLs.
- TWS uses in-memory subscriptions/definitions and the local `.contracts.json` cache.
  That regenerable runtime file is ignored by Git. No D1, KV, R2 or other database is used.

## Current features

### Paper fills, fees and margin

- Liquidity-class execution based on average dollar volume. Class parameters control
  spread capture, book walking and resting-limit queue waits. Lognormal waits and
  partial fills model joining the touch rather than immediately filling every limit.
  Large-cap queue defaults target roughly 4–8-second medians and 70–85-second means;
  these are model assumptions, not an execution guarantee.
- Within displayed size, marketable fills respect the visible quote; beyond it,
  estimated book walking affects price. Halts, stop triggers, limits and combos have
  their own fill paths. All orders and fills belong to the browser's simulated book.
- Streaming gateway ticks update prices and check resting orders immediately. REST
  polling remains the fallback/backfill; 1-minute historical bars reconstruct some
  missed limit/stop activity. Bar-based fills cannot reproduce queue priority.
- Editable commissions and pass-through fees: per-share and options minimums, SEC
  Section 31, FINRA TAF, ORF, OCC, option exchange/TAF and futures fees. Current defaults
  include SEC $20.60/million on sales and share TAF $0.000166 capped at $8.30.
  Verify fee schedules before using them as current broker estimates.
- Built-in Reg-T-style requirements, option offsets and configurable futures margin
  tables. These are not a reproduction of portfolio margin or SPAN.
- **Price a buy with IBKR** calls the broker's what-if endpoint for initial/maintenance
  margin impact and commission. It previews an order; it does not submit one.

### Streaming and option chains

- Gateway WebSocket subscription tracking, heartbeat, exponential reconnection and
  re-subscription; diagnostics show stream state and tick count.
- The gateway adapter probes the nearest **eight** option months for actual maturity
  dates, retaining weekly/daily dates. Further months use a monthly fallback.
  Probed months are no longer added twice to the expiration list.
- Calls/puts with greeks and IV. Provider values are used when available; missing
  greeks are computed with Black–Scholes, or IV is solved from mid price, and computed
  values are identified in the UI. This approximation is not an American-option model.
- Expected move from the at-the-money straddle; strategy presets for verticals,
  straddles, strangles, iron condors, butterflies and covered calls. Covered calls
  require held shares. Strike width, net delta/theta/vega and expiry break-evens are shown.
- Gateway futures lookup; separate editable futures margin settings.

## Data sources and limitations

The following describes the adapters' intended feeds, not a live verification of
current provider plans. Entitlements, limits, delays and endpoint availability can change.

### Official providers

| Provider | Intended feed / free-tier constraint | Chains | Bid/ask |
|---|---|---|---|
| Alpaca Basic | Real-time IEX; nominal 200 requests/minute | Yes, indicative options feed | Yes |
| Tradier sandbox | Approximately 15-minute delayed; nominal 120/minute | Yes | Yes |
| Finnhub | Delayed quote fallback; nominal 60/minute | No | No |
| Twelve Data | Free-tier daily budget; delay depends on plan | No | No |

Alpaca expects `KEYID:SECRET`. IEX covers only part of US volume and is not consolidated
NBBO. Tradier expects one sandbox access token; using Alpaca for quotes and Tradier for
chains is supported. Do not assume a provider's current plan or chain availability is
guaranteed by these adapters.

### Unofficial/no-key providers

| Provider | Intended data | Chains | Bid/ask |
|---|---|---|---|
| Nasdaq.com | Delayed site endpoints | Yes | Yes |
| Yahoo Finance | Delayed site endpoints | Yes, fragile | Sometimes |
| CNBC | Delayed site endpoint | No | Yes |
| Stooq | End-of-day fallback | No | No |

Nasdaq chains expose bid/ask and open interest but not greeks; the frontend computes
missing values. Yahoo uses cookie/crumb setup, host failover, retries and rate-limit
backoff, but option requests can still fail with 401. Undocumented endpoints can change,
block or disappear without notice. Use an official provider where reliability matters.

Cboe delayed chain scraping is deliberately absent because its published terms prohibit
automatic extraction. MarketData.app is not integrated; the original design noted that
per-option credit billing could exhaust a small free allowance in a single chain load.

Failover tries the selected source, usable keyless sources, and only the official source
whose key was supplied. It does not dial TWS unless TWS was selected. Responses identify
`served_by`, fallback source and the original failure. Missing bid/ask can be synthesized
from liquidity-class spreads and is labeled estimated. Delayed quotes mean stale-price
paper fills. Free-provider futures, streaming and broker what-if requests are refused
rather than invented.

## TWS / IB Gateway

Install `ib_async`, enable socket clients in TWS API settings, and **leave Read-Only API
ticked**. Keep TWS or IB Gateway running. Select the TWS data source on the Account tab.
Ports are scanned in order: TWS live 7496, TWS paper 7497, Gateway live 4001, paper 4002.

The adapter connects with `readonly=True`, constructs no order objects, and replaces
order-submitting/cancel/exercise methods on its IB instance with a function that raises.
Keep broker-side Read-Only API enabled as the authoritative extra safeguard; do not rely
on a Python library flag alone. Diagnostics report whether the wrapper methods are sealed.
The offline suite checks the wrapper seal, not the behavior of a live broker session.

The adapter minimizes account/position startup data and cancels account subscriptions
that the library may open. Streaming subscriptions are reused, definitions are cached,
and failed connection attempts have a cooldown. Request handling monitors silence/progress;
contract-definition requests have a longer quiet allowance because IBKR can pace repeats.
Delayed/frozen data handling and farm/entitlement diagnostics help explain empty chains.

Run `python3 check.py AAPL` to diagnose a real local TWS connection. It defaults to client
ID 78 so the app's client ID 77 can remain connected. This command is live and can display
account identifiers; sanitize its output before sharing.

### Client Portal session troubleshooting

- HTTP 410 means the brokerage session ended. The frontend attempts reauthentication,
  waits, checks auth status and retries once. A competing TWS/mobile login may displace it.
- Paper logins (`DU`/`DF`) may lack shared live-data entitlements. Configure data sharing
  in Client Portal for the paper username and check the required options subscription.
  The UI marks paper logins and persistent empty snapshots with a diagnostic.
- TWS and Client Portal are separate APIs. Client Portal needs its own gateway/login;
  it is also the current source of broker what-if previews. Excel DDE/RTD and FIX are not
  integrated.

## HTTP entry points and security

| Entry point | Purpose / parameters |
|---|---|
| `GET /`, `/index.html` | UI; `?t=TOKEN` signs in when access protection is enabled |
| `GET /manifest.json`, `/icon.png`, `/favicon.ico` | Public install/icon metadata; contain no account data |
| `GET /data/providers` | Supported-provider metadata |
| `GET /data/quote` | `provider`, `symbol`, optional `key` |
| `GET /data/search` | `provider`, `q`, optional `key` |
| `GET /data/chain` | `provider`, `symbol`, optional `expiry`, `key` |
| `GET /data/selftest` | Live provider diagnostic: `provider`, `symbol`, optional `key` |
| `GET /data/twsstatus`, `/data/depth?symbol=AAPL` | Local TWS diagnostics/depth |
| `GET /api/*` | Allowlisted account initialization, snapshots/history, secdef and futures reads |
| `POST /api/iserver/auth/status` | Gateway auth status |
| `POST /api/iserver/reauthenticate` | Gateway session recovery (optional `force=true`) |
| `POST /api/iserver/secdef/search` | Contract lookup JSON body |
| `POST /api/iserver/account/{accountId}/orders/whatif` | Preview JSON `orders`; never actual submission |
| `GET /ws` (upgrade) | Gateway streaming bridge |

`PAPER_ACCESS_TOKEN`, when set, is checked for GET, POST, DELETE and the WebSocket
upgrade. Cookie names and values must match exactly using constant-time token comparison.
Login sets an HttpOnly, SameSite=Lax cookie and redirects to remove the token from the
page URL. HTTPS reverse proxies must set `X-Forwarded-Proto: https` for a Secure cookie
and preserve the public Host header for same-origin validation.

Foreign browser origins are rejected, including in local no-token mode. The REST proxy
is an explicit allowlist: real order submission, modification, confirmation and deletion
are blocked even for authenticated callers. Request bodies are limited to 1 MiB and
unsupported/malformed framing is rejected. API/data responses use `Cache-Control: no-store`;
responses include no-referrer and nosniff policies. Application logs redact query strings
and no longer print the access token at startup.

These are incremental safeguards, **not a full security audit or a multi-user auth system**.
The gateway session is shared by the Python process. WebSocket messages still pass through
a minimal bridge; message filtering/protocol hardening is a follow-up. Browser-entered
provider keys still travel in query parameters: reverse-proxy/access logs must be configured
to redact them, or use a server environment key instead. The initial login URL also
contains the token before redirect, so host-side logs require care. Gateway TLS verification
is disabled for its local self-signed certificate; do not point it at an untrusted remote
endpoint. Public hosting requires HTTPS, an access token and a trusted reverse proxy.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `PAPER_PORT` | `8765` | HTTP port |
| `PORT` | unset | Host-provided port, takes precedence over PAPER_PORT |
| `PAPER_BIND` | `127.0.0.1` | Set `0.0.0.0` only when external access is intended |
| `PAPER_ACCESS_TOKEN` | unset | Shared private-app login token; required for public hosting |
| `PAPER_GATEWAY` | `https://localhost:5000/v1/api` | Trusted local gateway base URL |
| `PAPER_GATEWAY_WS` | derived from gateway | Gateway WebSocket URL |
| `PAPER_NO_BROWSER` | unset | `1` disables automatic browser opening |
| `PAPER_PROVIDER` | `alpaca` | Server fallback provider when the request does not specify one |
| `PAPER_PROVIDER_KEY` | unset | Server-side provider credentials |
| `PAPER_TWS_HOST` | `127.0.0.1` | TWS socket host |
| `PAPER_TWS_PORTS` | `7496,7497,4001,4002` | Ports to probe |
| `PAPER_TWS_CLIENT_ID` | `77` | App TWS client ID (`check.py` defaults to 78) |
| `PAPER_TWS_DATA` | `auto` | `auto`, `live` or `delayed` |

Additional tuning variables are documented beside their definitions in `tws.py`, including
silence allowances, chain paint intervals, line limits, definition TTL and reconnect cooldown.
The frontend explicitly sends its saved provider, so changing only the server default does
not override an existing browser's provider selection.

## Verification

Run from the repository root:

```sh
python3 -m unittest -v
node --test test_frontend.cjs
python3 -m compileall -q serve.py providers.py tws.py ws.py check.py
```

Node is needed only for frontend tests, not to run the application. Python tests create a
short-lived localhost HTTP server and mock all brokerage/provider calls. No credentials,
package installation or internet access are needed.

**2026-09-08:** 16 Python tests and 3 Node tests passed on Python 3.13 / Node 22. Coverage
includes authentication for each method, exact cookie matching, login redirect/cookie flags,
origin checks, public assets, allowed previews/data routes, blocked real order routes,
body limits/framing, credential-specific response caching, log redaction, finite numeric
normalization, the TWS wrapper seal and duplicate/weekly expiration handling.

The previous README mentioned 41 stub tests, but those test files were not present in the
initial GitHub checkout. Do not treat that historical count as reproducible coverage.
No live IBKR/TWS/provider integration or full interactive browser test was run during this
review. Offline tests do not establish fill realism or broker compatibility.

## Continuation notes / recommended next work

1. **Financial correctness first:** `settleExpired()` currently assumes a missing
   underlying quote means worthless and uses a current quote rather than historical
   expiry settlement. It also cash-settles options instead of modeling physical exercise
   and assignment. Review this before relying on expiry P&L. Add settlement, margin,
   stop/limit/bar-fill and multi-portfolio persistence tests before changing that engine.
2. **WebSocket robustness:** `ws.connect()` does not verify Sec-WebSocket-Accept and can
   discard the first frame if it arrives with upgrade headers. `recv_frame()` needs
   tests for interleaved control frames, fragmentation, size bounds, timeouts and clean
   shutdown. Consider a maintained transport dependency or carefully test fixes.
3. **Data integrity:** improve localStorage schema validation/recovery, key handling,
   cache size bounds and concurrent duplicate-request suppression. Browser/account data
   is not backed up by pushing this repository.
4. **Live integration validation:** test TWS connection/subscriptions and gateway/proxy
   behavior against actual entitled accounts, read-only. Recheck provider terms, rates,
   delays, fee schedules and response shapes. No live credentials are in this repository.
5. **Refactoring:** only split the large inline frontend after adding trading-engine/UI
   tests. Preserve the current vanilla JS + Python architecture unless a migration is
   explicitly requested. Cloudflare hosting would require a separate design decision.

### Preservation workflow (user requirement)

Commit and push all non-sensitive source, regression tests and continuation/reference
notes to the existing repository's **main** branch at useful checkpoints. A local commit
alone is insufficient if the sandbox disappears; verify the push succeeded. Do not create
a replacement repository or force-push over remote work. Files already tracked in Git,
including read-only reference documentation, remain preserved without artificial edits.

Keep secrets, environments, dependencies, generated caches and private account exports out
of Git. No external reference files were needed for this review; its findings are recorded
here. Read this README, START_HERE.md and DEPLOY.md when resuming in a fresh checkout.
