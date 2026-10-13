# Eurostat EA21 GDP: retain the latest qualified quarter

The live collector records the highest qualified reference quarter for the
existing `namq_10_gdp:Q.CLV_PCH_SM.SCA.B1GQ.EA21` series. A later successful
response containing only an older quarter cannot make that older quarter
eligible again. This knowledge survives failed responses and disk restarts.

The existing `release_frontier` metadata carries this period floor. For this
exact Eurostat series, `next_due_at` and its precision may both be `null`:
the API supplies no confirmed publication calendar. Unknown dates remain
unknown, and the existing hourly-check and maximum-age rules still apply.
ABS and Stats NZ frontiers continue to require their known release deadlines.

A legacy observation establishes the floor only if its earlier check was
eligible and its original URL, unit, frequency, seasonal adjustment, geography
and quarter agree. Revisions within the latest quarter remain admissible;
contradictory identities cannot satisfy its floor. Shared and bundled snapshots
validate and preserve the same strictly allowed public metadata.

Regression fixtures use synthetic future Q3 observations and clocks to reproduce
the rollback path. They do not represent a published Q3 value or an observed
provider rollback. CORE version, weights, score formulas, 2Y sources, historical
snapshots and the 100% pair gate are unchanged.
