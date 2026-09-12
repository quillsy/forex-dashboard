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
