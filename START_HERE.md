# Paper Desk — how to run it

## The short version

**Current default: IB Gateway streaming.** First run `python3 -m pip install -r
requirements.txt` (Windows: `python -m pip install -r requirements.txt`), then open
IB Gateway with its Read-Only API enabled. Start Paper Desk below. The app connects
and streams quotes automatically; you do not need to press Refresh or enable Auto.
The server and browser tab must remain running for paper order processing.

The ticket has quantity presets, Bid/Mid/Ask limit shortcuts and opt-in Alt+B/Alt+S
paper-order hotkeys. Stale, frozen, delayed or disconnected Gateway quotes cannot
fill orders. The status line explains pending prices or data-line limits.

**On your phone:** open the URL of your running, hosted Paper Desk instance. This
repository does not include a hosted artifact URL; see `DEPLOY.md` for Python-host
options. The server must remain running. A phone's browser has its own separate
paper account; GitHub does not back up browser storage.

**On a Mac:** unzip the folder, then **right-click `start.command` → Open** (right-click
the first time, not a double-click — macOS blocks anything downloaded from the internet
until you approve it once). A Terminal window opens and your browser follows.

If macOS says "cannot be opened because it is from an unidentified developer", that is
the same thing: right-click → Open → Open. Or open Terminal in the folder and run
`chmod +x start.command && ./start.command`.

**On Windows:** unzip, then double-click `start.bat`.

**Any system, from a terminal:** `python3 serve.py` in the folder.

Either way the app is at <http://localhost:8765>. Leave the Terminal window open while
you use it — closing it stops the server.

### If the Mac says Python is missing

`start.command` will tell you. Fix it with one line in Terminal:

```
xcode-select --install
```

That installs Apple's developer tools, which include Python 3. Or download it from
<https://www.python.org/downloads/>. Then install the IB Gateway adapter with
`python3 -m pip install -r requirements.txt`. The base HTTP server uses the standard
library; live Gateway streaming needs the pinned `ib_async` dependency.

---

## No, you cannot just open the HTML file

Double-clicking `paper_local.html` will show you the page, but it will have no data
and nothing will work. Here's why, because it matters:

The page calls same-origin `/api` and `/data` endpoints that do not exist when it
is opened from disk. Browser cross-origin restrictions also prevent many direct
provider requests. `serve.py` supplies those endpoints, serves the page from
`localhost`, and forwards allowed data requests. The base server uses only
Python's standard library; the optional TWS adapter needs `ib_async`.

So: **always start `serve.py`, never open the HTML directly.**

---

## Which data do you want?

You have four choices, and you can switch between them any time on the Account tab.

### 1. Free, no signup, no key — unofficial

Just run `python3 serve.py`, open the page, go to **Account → Market data source**,
set *Free data only*, kind *No key, unofficial scraping*.

Works immediately for quotes. The app does everything it can to keep this path alive —
browser-style cookie and crumb, retries, failover between Yahoo's two hosts, rate-limit
backoff, and a cache so it asks less often.

**But the option chain will fail sometimes**, with a 401 from Yahoo, and there is no
client-side fix for that. If you mostly want option chains, skip to option 2 — a free
Tradier token avoids Yahoo's cookie/crumb problem, though any provider can still fail.

### 2. Free, official, with a key — recommended

**Alpaca** is the best free option: real-time IEX quotes with a genuine bid and ask,
option chains with greeks, 200 calls a minute, and it's a documented API you hold a
key for.

1. Sign up free at <https://alpaca.markets>
2. Home → **API Keys** → generate a key. You get a Key ID and a Secret.
3. In the app: Account → Market data source → *Free data only*, kind *Official API*,
   provider **Alpaca**, and paste the key as `KEYID:SECRET` (both parts, one colon
   between them).

**Tradier** is the one to use for options — 15 minutes delayed, but real chains with
greeks across every listed expiry. Free sandbox account at
<https://developer.tradier.com>, then paste the access token on its own.

**You can mix them**, and for most people that is the right answer: quotes from Alpaca
(live), chains from Tradier (complete). Account tab → *Option chains from* → Tradier.

Two things to know about Alpaca's free tier: quotes are **IEX only**, a few percent of
US volume, so real-time but not the national best bid and offer; and its options feed
is **indicative** rather than full OPRA, which is why Tradier is better for chains.

### 3. Your own TWS or IB Gateway — the best data here

If you already run TWS, this is the one to use. Real-time ticks, IBKR's own greeks and
implied volatility on every strike, every listed expiry, and Level 2 depth.

1. `pip install ib_async`
2. In TWS: **Edit → Global Configuration → API → Settings** → tick **Enable ActiveX and
   Socket Clients**. Leave **Read-Only API** ticked. IB Gateway allows API connections
   already.
3. Leave TWS or Gateway running. There is no headless mode.
4. In the app: Account → Market data source → *Free data only*, kind **Your own TWS or
   IB Gateway**.

Ports are found automatically: TWS live 7496, TWS paper 7497, Gateway live 4001,
Gateway paper 4002.

**It is designed for paper trading only.** The adapter connects with `readonly=True`,
constructs no order objects, and replaces order-sending methods on its IB instance
with a function that raises. Keep **Read-Only API** enabled in TWS as the broker-side
safeguard; a library flag alone is not an order-transmission guarantee. The diagnostics
line reports `TWS: read-only, sealed` for the wrapper protections.

**No market data subscription?** Doesn't matter here. If the live feed comes back empty
the adapter switches to IBKR's delayed feed, which needs no entitlement, and labels the
data delayed. That is the simplest fix for a paper login with nothing shared to it.

### 4. Your IBKR account via the Client Portal gateway

Everything the free sources can't do: streaming tick data, futures, weekly and daily
option expirations, and IBKR's own margin engine via what-if previews.

1. Download the **Client Portal API Gateway** from
   <https://www.interactivebrokers.com/en/trading/ib-api.php> and unzip it.
2. Start it: `bin/run.sh root/conf.yaml` (Windows: `bin\run.bat root\conf.yaml`)
3. Open <https://localhost:5000> and log in. Your browser will complain about the
   certificate — that's expected, it's your own machine.
4. Start `python3 serve.py` as usual. It finds the gateway by itself.

Leave the data source on *IBKR gateway, fall back if it is down* and you get the good
data when the gateway is running and the free data when it isn't, with no
intervention.

---

## What differs between them

| | No key (scrape) | Alpaca (free key) | IBKR gateway |
|---|---|---|---|
| Quotes | ~15 min delayed | real time (IEX) | real time, streaming |
| Real bid/ask | sometimes | yes | yes, with depth |
| Option chains | yes | yes, with greeks | yes, with weeklies |
| Futures | no | no | yes |
| IBKR's own margin numbers | no | no | yes |
| Signup needed | none | free account | IBKR account + gateway running |

Everything else — the account, fills, margin engine, history — is identical.

---

## Troubleshooting

Look at the small grey line under "Paper Desk" at the top of the page. It names the
data source, whether the stream is live, and the last thing that failed. Almost every
problem is visible there.

- *"python3: command not found"* — install Python 3 from <https://python.org>, or on
  a Mac use `python3` from the Xcode command line tools.
- *Page loads but no prices* — check the grey line. If it says a provider error, the
  key is wrong or missing. Alpaca needs **both** parts: `KEYID:SECRET`.
- *"gateway unreachable"* — the IBKR gateway isn't running or you haven't logged into
  <https://localhost:5000> yet.
- *Port already in use* — `PAPER_PORT=9000 python3 serve.py`.
