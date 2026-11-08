# Live pit strategy (Strategy tab)

The Live page's **Strategy** tab answers one question during a race: *if this
car pitted now, where would it rejoin?* It is a deterministic estimate from the
current timing screen, not a forecast.

Code: `src/f1dash/processing/pit_strategy.py` (the calculation, pure) and
`src/f1dash/ui/strategy.py` (the panel). Tests: `tests/test_pit_strategy.py`,
several against `tests/fixtures/strategy/sao_paulo_2026_race.json`, a
trimmed recording of the live feed during the 2026 São Paulo Grand Prix.

## What it shows

- **The selected car now**: racing number, position, current tyre and its age,
  pit stops, gap to the leader and to the cars either side.
- **Estimated net pit loss**: a slider (15–35 s) starting at a generic 20 s
  assumption, with a button that resets it. This is the only time the
  prediction adds to the car's gap.
- **Observed pit lane time**: the median pit lane time of this race's stops,
  shown beside the slider for reference and never used as the net loss.
- **The rejoin**: the position before and after the stop, and the projected
  order around the rejoin point with each car's gap to the leader and its
  distance to the car that pitted. The car that pits is outlined.
- **Margins**: how much more net pit loss loses the next place, and how much
  less gains one. A tie goes to the car already there, so the place is lost at
  exactly the first margin and gained only past the second. A margin is not
  shown when that neighbour has no timed gap, when the place is a range, or
  when gaining would need a stop costing less than zero.
- **Scenarios**: the same calculation at the chosen loss and 2 s either side,
  for a quicker or slower stop. These are scenarios, not a confidence interval.
- The time since the feed's last update, and a warning past 30 s (the Live
  page's own threshold) that the order is from then, not now.

## How it works

1. **One time axis.** Every running car gets a time behind the leader. On the
   lead lap that is `GapToLeader`. A lapped car's `GapToLeader` reads `1 L`,
   but its `IntervalToPositionAhead` is still in seconds, so the axis continues
   by adding intervals down the order (recorded live: P16 read `1 L` with an
   interval of `+24.236` to P15 at `+63.785`, so 88.0 s).
2. **The stop.** The selected car's time grows by the net pit loss. Every other
   car keeps its time. The rejoin position is one more than the number of cars
   at or below the new time.
3. **Unknown places stay open.** A car with neither a gap nor an interval to a
   car with one is only known to sit between its neighbours in the order. If
   the rejoin point falls in that window, the panel shows a range (`P2–P3`)
   rather than picking one.

## Net pit loss and pit lane time

Two different times, kept apart throughout (`processing/pit_loss.py`):

- **Pit lane duration** (observed): pit entry to pit exit, stationary time
  included. The feed's `PitLaneTimeCollection` announces it for each stop and
  deletes it about 20 s later, so the adapter keeps every announcement
  (`SignalRLiveAdapter.pit_history`). On the Analysis page it comes from
  FastF1's `PitInTime` to `PitOutTime`.
- **Net pit loss** (assumed): how much longer the stop takes than staying out,
  that is the pit lane duration minus the time the car would have taken over
  the same stretch of track. A 25 s pit lane with a 5 s stretch on track is
  about a 20 s net loss.

Only the net loss moves the car. Adding the full pit lane duration would
overstate every stop by that stretch of track, and drop the car too far.

**The net loss is not measured.** Neither the live feed nor FastF1 reports
the time a car would have taken over the pit stretch on track. Nothing here
derives it, so the slider is an assumption and the panel says so. It starts at
a generic 20 s (`DEFAULT_NET_PIT_LOSS`), which is not a figure for any
circuit. Set it to the circuit's value before reading the prediction.

The observed median pit lane time (three or more stops between 12 and 45 s;
outside that is a drive-through, a repair or a red-flag wait) is shown as a
ceiling. A net loss at or above it is flagged, since the net loss leaves out
the on-track stretch. Live stops are counted from when the server connected
to the feed. No topic is added to the subscription and no request is made.

The per-circuit table the Analysis panel used to start from
(`PIT_LOSS_SEEDS`) was removed. It was hand-entered and never measured. It was
labelled as pit lane time but used as the loss, and at the one circuit checked
it disagreed with the data: 21.0 s for São Paulo, against a 24.5 s median pit
lane time over the seven green-flag stops recorded in the 2026 race.

## Assumptions

- Every other car keeps its current gap: unchanged relative pace through the
  stop (no undercut or overcut), and no one else pits.
- The net pit loss is the value on the slider, an assumption. The prediction is
  only as good as that figure.
- Positions are classification by time behind the leader at the timing line.
  A car that rejoins next to a lapped car on the road is not racing it.

## Limits (V1)

- **Safety car, VSC and red flag are unsupported.** A stop under them costs
  much less than under green and is not modelled, so the panel says so instead
  of predicting.
- **A car in the pit lane** has no trustworthy gap: mid-stop, the screen can
  still show its pre-stop gap (recorded live: VER `IN PIT` at P3 with 11.6 s,
  less than P2's 12.1 s). It keeps its place in the order but is placed only
  between its neighbours, and its own prediction is unavailable until it is
  back on track.
- Retired cars are left out of the order. Practice and qualifying are not
  supported.
- Gaps update per car as each passes a timing point, so the screen is briefly
  out of order during overtakes and stops. Gaps more than 0.5 s out of order
  are flagged.
- A neighbour within 1 s of the rejoin point makes the call "too close": a
  slow stop or a quick out-lap swaps them.

## Calibration still needed

- **Net pit loss per circuit.** One way to measure it from data the app
  already has: in-lap plus out-lap time, minus twice the driver's green-flag
  lap time either side of the stop. That uses FastF1 laps, `PitInTime` and
  `PitOutTime`, and the live lap history. It also counts the slow in-lap and
  the out-lap on cold tyres, which is part of the real cost of a stop, but
  traffic, fuel and tyre age add noise. It needs checking against known stops
  before it replaces the assumption, so it is not used yet.
- Until then the 20 s starting value is generic. Treat any prediction made
  with it as illustrative.

Out of scope for V1: tyre degradation, undercut and overcut pace, other cars'
stops, safety-car probability and full-race projections.
