# Preserve known publication deadlines

A successful reread of an old observation cannot cancel its previously
confirmed next-release deadline when the publisher page omits or changes the
calendar sentence. ABS labour/GDP and Stats NZ labour/GDP keep that knowledge
across invalid responses and process restarts, separately from current values.

`release_frontier` contains only the fixed original source/series identity,
reference date, latest qualified period, deadline and approved precision label. It contains no raw
provider response or private information. An unverified later deadline does
not extend an earlier deadline. A validated successor period replaces the old
deadline when a new deadline is known. A successor without a following date
retains the already fulfilled deadline and advances the minimum qualified
period independently; it still requires successful hourly checks. A later
rollback to an older period cannot erase that minimum, even after an error
or restart.
The reader independently enforces the preserved frontier if other deadline
fields are absent. Both public snapshot paths accept only these six bounded
fields for the four fixed currency/factor/series identities; extra, private,
malformed or future-reference fields are rejected. This does not assert that a publisher actually removed a
calendar entry; the failure path was reproduced with offline parser fixtures.

The fixed-EA21 unemployment series additionally keeps an August 2026 minimum
period from an exact Eurostat API observation, first checked on 2 October 2026
at 23:51:01.459554 UTC. This is an observation-time floor, not an inferred
publication hour or a different euro-area composition.

Source: https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/une_rt_m?freq=M&s_adj=SA&age=TOTAL&unit=PC_ACT&sex=T&geo=EA21&lang=EN&sinceTimePeriod=2025-01

Source payload SHA-256:
`48f99a384d6c94620b504163d24d58d84c44c582173041e32c49bb9da7200fd8`.

No original series, weights, CORE version, 2Y pipeline, pair gate, historical
calculation or backtest changes. Unresolved PMI/2Y source and reuse gates
remain blocked. Planned deadlines do not guarantee publication delivery or
forecasting performance; an officially evidenced postponement needs separate
verification before changing this preserved frontier.
