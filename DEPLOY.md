# Putting Paper Desk on the internet

Running it from GitHub gives you a URL that works on your iPhone anywhere, with no
laptop switched on. Three things to know before you start:

1. **The IBKR gateway cannot be reached from a cloud host.** It lives on your machine.
   A deployed copy runs on free provider data only. Keep the local copy for gateway work.
2. **Put a token on it.** `PAPER_ACCESS_TOKEN` gates the whole app; without it your API
   key is behind an open URL. Then you open `https://your-app/?t=YOUR_TOKEN` once and a
   cookie keeps you signed in.
3. **Your provider key lives in the host's environment**, never in the repo.

## Render (easiest)

1. Push this folder to a GitHub repo.
2. <https://render.com> → New → Web Service → connect the repo.
3. It reads `render.yaml`: free plan, `python serve.py`, no build step.
4. Set `PAPER_PROVIDER_KEY` in the dashboard (Alpaca: `KEYID:SECRET`). Copy the
   generated `PAPER_ACCESS_TOKEN`.
5. Open `https://your-app.onrender.com/?t=TOKEN` on your iPhone → Share → **Add to
   Home Screen**.

Free instances sleep after inactivity; the first hit takes a few seconds to wake.

## Hugging Face Spaces

New Space → **Docker** → push this folder. The `Dockerfile` is here. Set
`PAPER_PROVIDER_KEY` and `PAPER_ACCESS_TOKEN` as Space secrets. Spaces stay awake.

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
