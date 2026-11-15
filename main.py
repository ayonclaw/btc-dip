"""A mean-reversion bot for CBBTC/USDC over one-minute bars.

The dev window fell about 4% while swinging more than 8% peak to trough, so a bot
that buys dips and sells the bounce can end above water where buy-and-hold cannot.
Every decision comes from the bars already sent: no clock, no environment, no
unseeded random, so two runs of the same window produce the same action log.

Rules
-----
- Keep a trailing window (LOOKBACK bars) of CBBTC closes.
- A bar is a dip when its close sits DEVIATION_BPS below the window's mean, and a
  bounce when it sits EXIT_BPS above that mean (or when the position is up
  TAKE_PROFIT_BPS on its cost).
- Only one position at a time (the protocol allows no more). A buy spends
  STAKE_BPS of equity, capped by the cash on hand and the cap on one position's
  weight; the sells of one reply never fund its buys, so the reply is sized from
  the cash it holds.
- An entry that would fill more than MAX_SLIPPAGE_BPS past the close it saw is
  refused by the market; the bot sends it again while the signal still holds.
- A round trip costs about 26 bps in fees, slippage and impact, so a dip must
  clear that with MIN_EDGE_BPS to spare before it is worth taking.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import makefun_bars  # noqa: E402  (vendored shim, byte-identical to the kit's)

SYMBOL = "CBBTC"
BPS = 10_000

LOOKBACK = 30          # bars in the trailing mean
DEVIATION_BPS = 45     # how far under the mean counts as a dip
EXIT_BPS = 25          # how far over the mean takes the profit
TAKE_PROFIT_BPS = 40   # or this much over the position's own cost
STAKE_BPS = 9_500      # share of equity a buy commits (leaves room for fees)
MAX_SLIPPAGE_BPS = 60  # refuse a fill more than this past the close it saw
ROUND_TRIP_BPS = 26    # fees, slippage and impact on a full-size round trip
MIN_EDGE_BPS = 30      # a dip must clear the round trip by this much


def make_on_batch():
    closes = []

    def on_batch(state, batch):
        bar = None
        for candidate in batch.get("bars", []):
            if candidate.get("symbol") == SYMBOL:
                bar = candidate
        if bar is not None:
            closes.append(int(bar["c"]))
            if len(closes) > LOOKBACK:
                closes.pop(0)

        held = state.positions.get(SYMBOL)
        quantity = held.qty if held is not None else 0
        last = state.close(SYMBOL)
        if last is None:
            return []

        if quantity <= 0:
            # Look for a dip far enough under the mean to pay for the round trip
            # and still leave MIN_EDGE_BPS of edge.
            if len(closes) < LOOKBACK:
                return []
            mean = sum(closes) // len(closes)
            gap_bps = (mean - last) * BPS // mean
            if gap_bps < max(DEVIATION_BPS, ROUND_TRIP_BPS + MIN_EDGE_BPS):
                return []

            notional = state.equity * STAKE_BPS // BPS
            room = state.weight_room(SYMBOL)
            if notional > room:
                notional = room
            if notional > state.cash:
                notional = state.cash
            if notional < int(state.hello["fees"]["min_notional_quote_units"]):
                return []
            return [{
                "side": "buy",
                "symbol": SYMBOL,
                "notional_quote_units": notional,
                "max_slippage_bps": MAX_SLIPPAGE_BPS,
                "client_id": "dip",
            }]

        # Holding: sell the bounce, or the profit over its own cost.
        over_bps = 0
        if len(closes) >= LOOKBACK:
            mean = sum(closes) // len(closes)
            over_bps = (last - mean) * BPS // mean
        gain_bps = 0
        if held.cost:
            gain_bps = (state.value(SYMBOL, quantity) - held.cost) * BPS // held.cost
        if over_bps < EXIT_BPS and gain_bps < TAKE_PROFIT_BPS:
            return []

        return [{
            "side": "sell",
            "symbol": SYMBOL,
            "qty": quantity,
            "max_slippage_bps": MAX_SLIPPAGE_BPS,
            "client_id": "bounce",
        }]

    return on_batch


if __name__ == "__main__":
    makefun_bars.run(make_on_batch())
