# Paper Desk — standalone (IBKR Client Portal Gateway)

Runs entirely on your machine. No Claude connector, no cloud: the page talks to
IBKR's own REST gateway on localhost, and the account lives in your browser's
storage.

## One-time setup

1. Download the **Client Portal API Gateway** from
   <https://www.interactivebrokers.com/en/trading/ib-api.php> and unzip it.
2. Start it:
   - macOS / Linux: `bin/run.sh root/conf.yaml`
   - Windows: `bin\run.bat root\conf.yaml`
3. Open <https://localhost:5000> and log in with your IBKR credentials.
   (Your browser will warn about the self-signed certificate — that's expected;
   it's your own machine.)

## Every time

```
python3 serve.py
```

Opens <http://localhost:8765>. Python 3 only — no packages to install.

## What it does

- `serve.py` serves the page and proxies `/api/*` to `https://localhost:5000/v1/api`,
  so everything is same-origin and CORS never comes up. The gateway's self-signed
  certificate is accepted because the connection never leaves your machine.
- The page uses the gateway for contract search, snapshots, option chains and the
  futures ladder. Quote fields come from CPAPI field ids (31 last, 84/86 bid/ask,
  7635 mark, 7296 prior close, and so on).
- Your account — cash, positions, orders, fills, equity history — is saved to
  browser localStorage under `paperAccount`. Clearing site data wipes it, so use
  Export on the History tab if you want a backup.

## Files

| File | What it is |
|---|---|
| `paper_local.html` | the whole app, one self-contained file |
| `serve.py` | local server: static page, REST proxy, WebSocket bridge |
| `ws.py` | RFC 6455 codec used by the bridge (both directions) |
| `providers.py` | free market-data providers, used when the gateway is off |
| `start.command` / `start.bat` | double-click launchers for macOS/Linux and Windows |

All three must sit in the same folder.

## Accuracy

- **Liquidity-class fills.** Every instrument is classified by its 90-day average
  dollar volume, and the class sets how much of the spread a marketable order
  really gives up and how long a resting limit waits at the touch. A limit that
  merely joins the bid does not fill on arrival — it draws a wait from a
  lognormal fitted to measured fill times (median ~4-8s, mean ~70-85s for large
  caps) and fills in pieces as the size at the touch turns over. Price trading
  *through* your limit clears the queue and fills you immediately. Within the
  displayed size you never do worse than the quote you can see; beyond it you pay
  for walking the book. All parameters and their sources are on the Account tab.
- **Streaming quotes.** `serve.py` bridges the browser to the gateway's WebSocket,
  so prices arrive as ticks rather than being polled. Subscriptions follow whatever
  the page is showing, the session is kept alive with the gateway's `tic` heartbeat,
  and a dropped socket reconnects with exponential backoff and re-subscribes. The
  diagnostics line shows the stream state and a live tick count. REST polling stays
  as the fallback and backfill.
- **Tick-driven fills.** A resting order is checked on the tick that moves the
  price, not on the next poll, so a limit that becomes marketable fills at once.

- **Weeklies and dailies.** The gateway exposes option *months*; the adapter
  probes each of the four nearest months for its real maturity dates, so weekly
  and daily expirations appear alongside the monthly (which is flagged as such).
- **Bar-reconstructed fills.** Ticks can still be missed while the page is closed
  or the socket is down, so on every refresh the adapter also pulls 1-minute bars
  and checks whether the price traded through your order in the gap. Limits fill at the limit, never better — the bar proves the price
  traded, not that you were at the front of the queue.
- **Real commissions and fees.** IBKR's commission plus the pass-throughs it is
  billed: SEC Section 31 at $20.60 per million (sales only, the rate effective
  4 April 2026), FINRA TAF at $0.000166/share capped at $8.30, options ORF, OCC
  clearing, exchange fees and option TAF. All editable on the Account tab.
- **IBKR's own margin, on demand.** "Price a buy with IBKR" on the ticket calls
  the gateway's what-if endpoint, which returns the real initial and maintenance
  margin impact and commission for that exact order, computed by IBKR — including
  portfolio margin and SPAN, which the built-in Reg-T model cannot reproduce.
  What-if prices an order; it never places one. The paper account still fills
  against its own book.

## When the gateway is not running

The Account tab has a **Market data source** panel with two decisions: whether to use
the gateway at all, and if not, whether the free data should come from an official
API you hold a key for or from unofficial scraping that needs no signup.

**Official, free, needs a key**

| Provider | Delay | Limits | Chains | Bid/ask |
|---|---|---|---|---|
| Alpaca (Basic) | real time (IEX) | 200/min | yes, with greeks | yes |
| Tradier (sandbox) | 15 min | 120/min | yes, with greeks | yes |
| Finnhub | ~20 min | 60/min | no | no |
| Twelve Data | up to 4h | 800/day | no | no |

**Which to pick.** Alpaca for quotes: it is the only free source that is both live and
legitimate, and it publishes a real bid and ask. Paste its credentials as
`KEYID:SECRET`. But its free options feed is *indicative* rather than full OPRA, so if
you trade options, point the chain at **Tradier** instead — its sandbox serves real
chains with greeks across every listed expiry, 15 minutes delayed. The Account tab lets
you set the two independently: quotes from one provider, chains from another.

One caveat worth knowing about Alpaca's free tier: quotes come from **IEX only**, which
is a few percent of US volume. They are real-time and genuinely tradeable prices, but
they are not the national best bid and offer, so the spread you see can be wider than
the one you would really trade against.

**Unofficial, no key**

| Provider | Delay | Limits | Chains | Bid/ask |
|---|---|---|---|---|
| Nasdaq.com | ~15 min | undocumented | **yes** | yes |
| Yahoo Finance | ~15 min | undocumented | yes | sometimes |
| CNBC | ~15 min | undocumented | no | yes |
| Stooq | end of day | be gentle | no | no |

Nasdaq is the useful addition: it publishes a full option chain, every expiry, with
bid/ask and open interest, and no key. It does not publish greeks — those get computed
here with Black-Scholes from the mid price, and are marked as computed.

These are undocumented endpoints that can change, rate-limit or block without notice,
and that the provider never agreed to serve. They are here because they work today,
not because they are dependable. The app labels them unofficial wherever they appear.

Yahoo is handled as carefully as it can be: the app fetches a session cookie and crumb
the way a browser does, retries on a stale crumb, fails over between the `query1` and
`query2` hosts, and backs off on a rate limit. That makes quotes fairly reliable. The
**option chain is the fragile part** — Yahoo returns 401 for it intermittently, and no
amount of client-side care fixes that.

**What is deliberately not here.** Cboe publishes delayed option chains as public JSON,
with greeks, for every expiry — technically the best free source there is. Their terms
forbid it in plain words: *"it is strictly prohibited to download delayed quote table
data from this web site by using auto-extraction programs/queries and/or software."* So
it is not in the app. MarketData.app's free tier does allow chains but bills one credit
per option symbol against a 100/day budget, which one chain load exhausts.

The honest summary: **no single free no-key chain is dependable**, but there are now two
of them and the app tries both. If Yahoo 401s, Nasdaq usually carries it. A free Tradier
sandbox token still removes the uncertainty entirely.

## Which IBKR APIs this uses

IBKR publishes four: the **Web API** (Client Portal), the **TWS API**, the **Excel API**
and **FIX**. This app can use two of them, and you pick per session.

**TWS API** — the better data, and the default choice if you run TWS. Real-time ticks,
IBKR's own greeks and implied volatility per contract, complete chains, Level 2 depth,
and a delayed-feed fallback that works with no subscription at all. It reaches TWS or IB
Gateway over the local socket via `ib_async`, so both must be running. `serve.py` talks
to it in-process and hands results to the page.

**Client Portal Web API** — REST plus a WebSocket on `https://localhost:5000/v1/api`,
proxied by `serve.py`. Needs the separate Client Portal Gateway and a browser login, and
gives fewer greeks. Still the one used for what-if margin previews.

Excel is DDE/RTD on Windows and FIX is institutional; neither suits a browser page.

### The TWS session is read-only

Three independent guarantees, each tested:

1. it connects with `readonly=True`, so `ib_async` refuses to transmit orders;
2. nothing in `tws.py` imports or constructs an order object — there is no code path;
3. `placeOrder`, `cancelOrder`, `reqGlobalCancel` and `exerciseOptions` are replaced on
   the live session with a function that raises, so a bug cannot reach the wire either.

The diagnostics line reads `TWS: read-only, sealed` when all three hold.

### If the gateway returns 410

410 means the brokerage session ended underneath you — after idling, or because the same
username signed in somewhere else (TWS or the mobile app will kick the gateway). The app
now catches it, calls `/iserver/reauthenticate`, waits for `/iserver/auth/status` to come
back authenticated, and retries the request once. The diagnostics line counts how many
times that happened.

### If you logged the gateway into a paper account

Paper logins do **not** inherit market data subscriptions from the funded account, so
snapshots come back empty and option chains fail. Fix it in Client Portal: Settings →
Account Configuration → Paper Trading Account → *Share real-time market data* → yes, for
your paper username. Options also need OPRA on the funded account for the sharing to
produce chains. The app detects a paper login (accounts beginning `DU`/`DF`), shows
`paper login` in the diagnostics, adds `NO MARKET DATA` when snapshots keep coming back
empty, and spells out this fix in the chain error.

## Failover

Sources are tried in order rather than failing outright. If the one you picked is down,
rate-limited or refusing, the app tries the next usable source — always the keyless ones,
plus whichever provider your key belongs to — and the diagnostics line names who actually
served the data. Quotes are cached for 15 seconds, searches for 5 minutes and chains for
90 seconds, so a free tier's rate limit is much harder to hit.

Where a source publishes no bid/ask, the spread is synthesised from the instrument's
liquidity class (~2 bps for the most liquid, ~130 for the thinnest) and the quote is
labelled *estimated* rather than passed off as real depth. Anything delayed means fills
are struck against stale prices. Futures, streaming and the what-if margin preview need
the gateway and are refused on a free provider rather than faked.

## Option chain

- Greeks and implied volatility on every strike. Where the source publishes them
  (Alpaca, Tradier, Yahoo) they are quoted; where it publishes only implied volatility
  (IBKR) the greeks are computed with Black-Scholes and marked with an asterisk. Where
  even IV is missing it is solved from the mid price.
- **Expected move** for the expiry, taken from the at-the-money straddle.
- **Strategy presets** build the legs for you: call and put verticals, straddle,
  strangle, iron condor, butterfly and covered call, centred on the money, with a
  strike-width control. The covered call refuses unless you actually hold the shares.
- The builder shows **net delta, theta and vega** for the whole position and the
  **break-even points**, scanned across the payoff at expiry.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `PAPER_PORT` | `8765` | port the page is served on |
| `PAPER_GATEWAY` | `https://localhost:5000/v1/api` | gateway base URL |
| `PAPER_GATEWAY_WS` | derived from `PAPER_GATEWAY` | gateway WebSocket URL |
| `PAPER_NO_BROWSER` | unset | set to `1` to stop it opening a browser tab |
| `PAPER_PROVIDER` | `alpaca` | default fallback provider |
| `PAPER_PROVIDER_KEY` | unset | API key for providers that need one |

## Caveat

The adapter was built against IBKR's published Client Portal API shapes and
tested against a stub that mimics them, including a stub WebSocket server — 41
tests across the local build covering search, quote parsing, chains, weeklies,
what-if, tick fills, bar fills, socket drop and reconnect, provider fallback and
persistence. The free providers are likewise coded against their documented shapes
and tested against a stub, not against the live services. It
has **not** been run against a live gateway — if an endpoint answers differently in practice, the diagnostics line
under the account name shows the failing call and its status code.
