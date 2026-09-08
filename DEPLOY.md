# Putting Paper Desk on the internet

Deploying this GitHub repository to a Python-capable host can give you a URL that
works on your iPhone without your laptop running. GitHub alone does not run the server.
This stack cannot use the platform's Cloudflare preview/one-click deploy as-is.
No production URL was verified during the 2026-09-08 review.

**For the new IB Gateway workstation:** run Paper Desk on the Gateway machine,
or provide a trusted private connection to it. Never expose the Gateway socket to
the public internet. Hosted free-provider use is still available through Account
settings, but it is not equivalent to the default live Gateway mode.

The streaming endpoint `/data/stream` uses Server-Sent Events. Reverse proxies
must disable buffering and allow connections beyond the 10-second heartbeat.
The app and browser tab must stay running; this is not unattended server-side trading.

Three things to know before you start:

1. **The IBKR gateway cannot be reached from a cloud host.** It lives on your machine.
   A deployed copy runs on free provider data only. Keep the local copy for gateway work.
2. **Put a token on it.** `PAPER_ACCESS_TOKEN` protects the page, data, REST proxy and
   WebSocket upgrade (only install icons/manifest are public). Without it, anyone who
   can reach the server can consume your data-provider quota. Open
   `https://your-app/?t=YOUR_TOKEN` once: it sets an HttpOnly cookie and redirects to
   remove the token from the page URL. Use a strong, URL-safe random token.
3. **Your provider key lives in the host's environment**, never in the repo. Keep
   `.env` files, credentials and private browser-account exports out of Git.

Use HTTPS and a trusted reverse proxy that preserves the public `Host` header and
sets `X-Forwarded-Proto: https` (which enables the Secure cookie flag). Application
logs redact query strings, but your hosting proxy must also avoid logging query
credentials: the initial login URL contains a token, and browser-entered provider
keys currently use query parameters. Prefer a server environment key.

The REST proxy allows only the UI's data/session endpoints and what-if previews,
not real order writes. This is a single-user app sharing one gateway session, not
a multi-user brokerage service. See README.md for remaining security/transport risks.

**Backups:** pushing source to GitHub does not preserve the account stored in your
phone's or desktop's localStorage. Keep separate private account exports.

## Render (easiest)

1. Push this folder to a GitHub repo.
2. <https://render.com> → New → Web Service → connect the repo.
3. It reads `render.yaml`: free plan, installs `requirements.txt`, then runs `python serve.py`.
4. Set `PAPER_PROVIDER_KEY` in the dashboard (Alpaca: `KEYID:SECRET`). Copy the
   generated `PAPER_ACCESS_TOKEN`.
5. Open `https://your-app.onrender.com/?t=TOKEN` on your iPhone → Share → **Add to
   Home Screen**.

Free instances sleep after inactivity; the first hit takes a few seconds to wake.

## Hugging Face Spaces

New Space → **Docker** → push this folder. The `Dockerfile` is here. Set
`PAPER_PROVIDER_KEY` and `PAPER_ACCESS_TOKEN` as Space secrets. Configure the port
expected by the host (Spaces typically use 7860 via `PORT` or `app_port`); sleep and
availability depend on the hosting plan.

## Fly.io / Railway / any Docker host

The `Dockerfile` is all they need. Bind is already `0.0.0.0` and the port comes from
`$PORT`.

## What about Streamlit?

Streamlit Community Cloud is free and GitHub-native, so it is a natural thought — but it
cannot host this app as it stands. Streamlit serves one thing: a Streamlit script. It has
no way to expose the `/api` and `/data` endpoints this page needs, and a browser will not
let the page call Alpaca or Tradier directly (CORS again). The proxy is the whole point of
`serve.py`.

Making it a Streamlit app would mean rewriting the engine — fills, queue model, Reg-T
margin, greeks, history — from JavaScript into Python, and accepting Streamlit's rerun
model, which reloads the script on every click. Live quotes and tick-driven fills do not
survive that well.

If you want it anyway, say so and I will port it: the logic is all specified and tested,
so it is transcription rather than invention. But Render or a Space gives you the same
"push to GitHub, get a URL" outcome today, with everything intact.

## Environment variables

| Variable | Meaning |
|---|---|
| `PORT` | set by the host; `serve.py` honours it |
| `PAPER_BIND` | `0.0.0.0` when hosted, `127.0.0.1` locally |
| `PAPER_ACCESS_TOKEN` | required to open the app; use one when public |
| `PAPER_PROVIDER` | `alpaca`, `tradier`, `finnhub`, `twelvedata`, `yahoo`, `stooq` |
| `PAPER_PROVIDER_KEY` | the key; Alpaca wants `KEYID:SECRET` |
| `PAPER_NO_BROWSER` | `1` on a server, so it does not try to open a browser |
