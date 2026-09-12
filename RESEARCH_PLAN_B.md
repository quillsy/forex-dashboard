# Separate source research

Decision: user-authorized Plan B, 12 September 2026. Production model remains
`CORE_V2_8_2026_09`. Research observations never contribute to its coverage,
scores, pair eligibility, badges or historical snapshots.

## BIS NZD comparison

The keyless BIS policy-rate API provides a dated NZ Official Cash Rate
observation. Daily observation frequency is distinct from weekly distribution.
Neither the last observation date nor retrieval time proves the current RBNZ
decision or its effective date. The panel therefore reports those unknowns,
observation age and collection status separately and generates no signal.

The existing collector workflow invokes `python collect_research.py`; the
research collector performs at most one attempt per 24 hours using persisted
attempt metadata. Page views do not call BIS. Failed or invalid responses retain
the previous artifact and publish a fixed error code, without exception text.
The panel recomputes age when read; an old success is not a new verification.
GitHub scheduling remains best effort. A 14-day observation warning and a
24-hour collection interval are local research rules, not an official SLA or
CORE freshness allowance.

The 24-hour suppression depends on persisted attempt metadata. If a workflow
cannot push that metadata, a subsequent runner can repeat the request. This is
not an absolute distributed quota guarantee; GitHub run failures remain part
of the operator's deployment checks.

Only `research_data/bis_nz_policy.json` and `research_data/status.json` are
included in the collector's explicit research file allowlist. No credentials
are needed. Test fixtures are synthetic; actual collected observations carry
their retrieval time and source-response digest.

Source attribution: Bank for International Settlements, Central bank policy
rates; Reserve Bank of New Zealand. German descriptions are not an official
BIS translation. No endorsement is implied and no additional fee is charged
for these statistics. Terms checked on 12 September 2026:

- https://data.bis.org/help/legal
- https://data.bis.org/topics/CBPOL
- https://www.bis.org/statistics/cbpol/cbpol_doc.pdf

## Direct EA21 industry confidence

The direct European Commission DG ECFIN series distributed by Eurostat is
`ei_bsin_m_r2 / M / BS-ICI / SA / BAL / EA21`. This monthly survey balance
is seasonally adjusted, not calendar adjusted. It is neither PMI nor the
OECD harmonised confidence index. Source publication and API agree for
January–August 2026; August is -5.3 balance points.

The last reviewed full release is 28 August 2026, the next scheduled full
release is 29 September. The September 22 date concerns flash consumer
confidence and does not apply here. The calendar lists 11:00 without an
explicit timezone on the reviewed sheet; no exact UTC time is inferred.
The frozen date evidence is not extrapolated into future release calendars.
After its scope expires, display marks calendar coverage as unknown and
requires a recent successful hourly research retrieval for technical freshness.
An hourly retrieval still cannot prove the absence of a newer publication.

`python collect_ec_industry.py` stores the separate artifact and fixed status
codes in `research_data/ec_industry.json` and `ec_industry_status.json`. It
uses persisted hourly attempt suppression and keeps valid data after failures.
The workflow uses the same serialized job as CORE collection. Page views do
not fetch data. As with BIS, failed status publication can allow a repeat
request on the next runner.

Reuse is supported by Eurostat general statistical-data terms and the EC
release attribution notice for this EU aggregate. This does not establish
rights to individual survey microdata or third-party US indicators.

- https://ec.europa.eu/eurostat/web/main/help/copyright-notice
- https://ec.europa.eu/eurostat/cache/metadata/en/ei_bcs_esms.htm
- https://economy-finance.ec.europa.eu/document/download/a766aa07-8863-471f-8862-1e17f33cc134_en?filename=bcs_2026_08_en.pdf
- https://ec.europa.eu/economy_finance/db_indicators/surveys/documents/calendar/Publication%20dates%202026.pdf

The statistical annex has a contradictory cover date (2025) although its
title, table, listing and main release identify August 2026. Date extraction
therefore must not rely solely on that cover. Historical seasonal adjustments
are revised: stored current history does not establish past tradable vintages.

## OECD and manual evidence

OECD business confidence and industrial production remain candidates awaiting
series-level rights, original frequency, territory and publication checks.
BCI is not PMI. EA20 is not the current EA21 CORE territory. No OECD numerical
data are published by this integration. A successful API response is not proof
of either completeness or permission to redistribute third-party inputs.

Manual OCR intake remains a separate local evidence prototype. File hashes and
two official URLs alone cannot establish the truth or reuse rights of their
contents. No emergency override is used to promote these records.

## Research acceptance before any proposed model

Record exact source definitions and an approved publication contract first.
Capture actual availability timestamps and revisions. Where historical release
vintages cannot be established, use prospective observations rather than a
retrospective claim that today's history was available then.

Before evaluating a trading hypothesis, freeze its feature transformations,
direction rules, baseline, 5/10-trading-day horizons, exclusions, costs,
chronological splits, minimum effective sample and uncertainty method. Account
for overlapping holding periods. Report availability and economic performance
separately. No performance experiment has been completed by this panel and no
percentage improvement is claimed. Promoting any new definition, factor or
model into CORE requires explicit user approval.

## Remaining project acceptance

This research panel does not close missing production factors. Each CORE gap
still requires its own source, rights, metadata and freshness evidence. Overall
completion additionally requires regression results, successful production
collection and direct verification of the deployed display.

## Prospective research states

The two separate files `research_data/bis_nz_policy_vintages.json` and
`research_data/ec_industry_vintages.json` retain the states actually observed
by the research collectors. These are prospective records, not reconstructions
of earlier publication vintages. Initial historical observations all become
known to this archive together at the actual collection time.

`first_observed_at` is captured after response validation; `retrieved_at`
retains the source request-start timestamp. Neither is an original publication
time. No success rate, trade execution or backtest is produced by the archive.

The semantic snapshot preserves series definition, observations and missing
periods. Retrieval age, formatting and provider-wide update time do not create
new economic states. Compare only with the immediately preceding state:
A -> B -> A is three events. A rolling-window change is a state change, not
necessarily an economic revision. Current windows do not prove values outside
those windows.

Each source's existing collector lock protects journal, latest artifact and
status writes. Validate history before appending; write journal first, latest
artifact second and anchored status last. Corrupt or unexpectedly missing
history must not silently become a new empty archive. Fixed error codes avoid
publishing exception content. Due-aware restart behavior remains in force.

Hash links and a status anchor detect accidental edits/truncation relative to
that anchor. They are not signatures or proof against an attacker rewriting
both history and anchor. Earlier trusted Git commits provide a separate audit
reference. If publication fails, a runner-local capture is not guaranteed to
survive; no claim of externally durable storage is made until publication.

Only the two exact journal paths are added to the workflow's publication
allowlist. Source rights and attribution are the same as for their validated
research observations. Private notes, credentials and arbitrary source text
are excluded. This does not alter existing CORE snapshots or backtesting.
