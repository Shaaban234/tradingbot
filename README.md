# Cloud trading bot (Vercel, Mumbai)

The paper bot's dip-buying rules (v6.2) and safety guards, running every 15 minutes on Vercel's free plan.
It starts on the **Binance testnet (fake money)**. Real money only after you change `BOT_MODE`.

## What it does each run
1. Scans the 150 most-traded Binance USDT coins (real market prices).
2. Checks open trades. Each one has its **stop-loss and take-profit as real orders on Binance**, so it stays protected
   even if Vercel is late or down. The stop is moved up (breakeven at 1.5R, then trailing), and trades are closed
   after 24h (48h if in profit) or on bad news.
3. Safety guards: token unlocks, whale selling, coins moving together, daily loss pause (-3%), emergency stop (-25%).
4. Buys dips when ≥2 of 4 oversold signals agree, at most 2 coins, `TRADE_USDT` each, never beyond `BUDGET_USDT`.
5. Posts a Discord update; a daily summary after 9 PM Pakistan time.

## Setup (one time)
1. **Storage:** Vercel project → Storage → Create → *Upstash for Redis* (free) → connect it to this project.
2. **Testnet key:** log in at https://testnet.binance.vision with GitHub → Generate HMAC key.
3. **Environment variables** (Vercel → Settings → Environment Variables):

| Name | Value |
|---|---|
| `BOT_MODE` | `testnet` (later: `approval`, then `live`) |
| `BINANCE_KEY` / `BINANCE_SECRET` | testnet key for now. For real money: a key with **Spot trading only, withdrawals OFF**, restricted to Vercel's IPs if possible |
| `DISCORD_WEBHOOK` | your Discord webhook URL |
| `DISCORD_USER_ID` | your Discord user ID (for @mentions) |
| `CONTROL_TOKEN` | a long random password for status/control links |
| `CRON_SECRET` | another long random string (Vercel sends it to the 15-min schedule) |
| `BUDGET_USDT` / `TRADE_USDT` | optional, default 12 / 6 |

4. Redeploy. The schedule appears under Settings → Cron Jobs / Schedules.

## Links (replace `YOUR-APP` and `TOKEN`)
- Status: `https://YOUR-APP.vercel.app/api/status?token=TOKEN`
- Run a check now: `/api/run?token=TOKEN`
- Pause new buys: `/api/control?token=TOKEN&action=pause` · Resume: `action=resume`
- Sell a coin now: `/api/control?token=TOKEN&action=close&symbol=SOL`

## Modes
- `testnet`: fake money on Binance's test exchange (some coins are missing there and get skipped).
- `approval`: real money, but every buy needs you to tap the link in Discord within 30 min.
- `live`: real money, automatic.

## Test offline
`python3 tests/sim.py`: fake exchange and prices; checks buying, protection, stop-loss, trailing, failure handling.
