# Live bot changelog (for the weekly report)

## Week of 2026-11-23
- 2026-11-24 01:47 PKT: went live on the user's Binance account. Budget $20, max 2 coins, rules = paper bot v6.2.
- 2026-11-24 01:55 PKT: full paper-bot parity: 29-strategy score for the 24h-extend rule and runner mode, Reddit danger veto, risk-based sizing (stop-out loses ~1.5% of balance, min $6).
- 2026-11-24 02:05 PKT: paper bot made quiet (comparison only until the 30 Nov review, then switched off).
- 2026-11-24 02:45 PKT: whale watcher fix (both bots): busy coins returned only the last 1000 trades (~2 min on BTC), so "last hour" was really a few minutes and noisy (BTC showed "dumping" on 2 min, not on 14 min). Now pages back up to 6000 trades; most coins get the full hour.
- 2026-11-24 03:09 PKT: FIRST LIVE TRADE: bought $6.00 of USUAL at $0.01109 (paper bot bought the same coin at $0.01105 the same hour). OCO stop $0.01045 / target $0.01237 live on Binance. Research: no red flags.
- 2026-11-24 03:25 PKT: Discord report now shows cash left, money in trades and the Binance USDT balance; trade events listed right after the header (user request).
- 2026-11-24 07:09 PKT: LIVE TRADE: bought $12.04 of JST at $0.14504 (stop -2.5% / target +5%; paper-rule size: 1.5% risk at a 2.5% stop = $12). Paper bot didn't buy it only because it already held 2 coins. Research: no red flags (Tron DeFi gov token, buyback & burn ongoing).
- 2026-11-24 11:46 PKT: budget raised $20.00 -> $20.15 at the user's request (use the whole USDT balance; fees come out of the coins received, so no cash cushion is needed).
- 2026-11-25 01:45 PKT: improvement tests (user asked how to improve). 151-day backtest, 119 coins, both halves. NO RULE CHANGED, every variant lost to the current rules (+24.4% full, +4.7%/+19.5% halves):
  - Longer holds (36h, 48h, extend on any profit, 72h cap): -8% to -36%. The paper trades still rising at 24h were the exception, not the rule.
  - Tighter stops (max 5% / 4.5% / 4%, 2.0xATR): -18% to -45%; the coins this bot buys need room.
  - Time-stops for trades going nowhere (exit after 8h/12h, or tighten to -2.5%/-3%): worse in the 1st half, better in the 2nd: inconsistent, rejected.
  - Robustness warning: nearby hold times swing a lot (22h -4%, 24h +24%, 26h -47%). With only 2 slots, one different exit changes every later trade, so backtest % are noisy; the real edge is smaller than +24% suggests. Keep the budget small until 50+ live trades.
