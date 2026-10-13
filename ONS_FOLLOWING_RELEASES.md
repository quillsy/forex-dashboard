# ONS live release boundaries

The live reader and ONS adapter keep the original GBP annual CPI series
`D7G7/MM23` and quarterly, seasonally adjusted real GDP YoY series
`IHYR/PN2` / `IHYR/QNA`. Re-fetching an old period cannot qualify it after a
known following release is due. These are availability checks; CORE weights,
model version, pair completeness rules and historical calculations stay the
same.

The ONS time-series release pages and calendar were checked on 13 October
2026. They describe scheduled releases, not guaranteed API delivery times:

| Publication | Planned boundary | Required new evidence |
|---|---|---|
| [September CPI](https://www.ons.gov.uk/releases/consumerpriceinflationukseptember2026timeseries) | 21 October 2026, 06:00 UTC / 07:00 London (BST) | Original D7G7/MM23 September or later period, actual description release date and successful check since the boundary |
| [Q3 GDP first estimate](https://www.ons.gov.uk/releases/gdpfirstquarterlyestimateukjulytoseptember2026timeseries) | 12 November 2026, 07:00 UTC / 07:00 London (GMT) | Original IHYR Q3 or later period with a corresponding release vintage and successful check |
| [Q3 GDP national accounts revision](https://www.ons.gov.uk/releases/gdpquarterlynationalaccountsukjulytoseptember2026timeseries) | 22 December 2026, 07:00 UTC / 07:00 London (GMT) | For Q3, IHYR/QNA with the revision vintage and successful check; an unchanged numeric value is permitted |

The later GDP revision never postpones the November first-release boundary.
Before that revision, a valid QNA Q3 November vintage is allowed as well as
PN2 Q3. Both families are still fetched, parsed and checked for conflicts; an
older QNA Q2 component does not invalidate an otherwise qualified PN2 Q3
result. The existing Q2 revision rule and PN2-only official transport fallback
remain in place.

Early new periods and same-day vintages are rejected before the evidenced
publication hour. Reader checks also apply to legacy records without cached
deadlines and after an outage or restart. ONS `releaseDate` is a London
calendar date, so `published_at` stays unknown. CPI current metadata is kept
separately in DataFrame attributes; historical values, row `updateDate`
mapping and PIT limitations are preserved. Unknown following calendars still
require a successful freshness check within one hour. A source outage cannot
advance the last successful check.

These dated calendar boundaries must be reviewed if ONS changes its schedule;
they do not establish a permanent calendar service or a release-latency SLA.
Future numeric observations are not asserted by the schedule or by synthetic
test fixtures. ONS source attribution and its [Open Government Licence
notice](https://www.ons.gov.uk/help/termsandconditions) continue to apply;
calendar feeds and provider payloads are not republished by this change.

Offline regressions cover old and early periods, exact boundary hours,
release-day and source identity checks, post-release checks, old QNA
components, same-release conflicts, legitimate unchanged revisions, fallback
transport, restart/outage paths, current CPI metadata and the strict pair
gate. GitHub CI runs these tests with HTTP, socket and DNS attempts forbidden.
