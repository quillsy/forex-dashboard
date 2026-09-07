# Data access and reliability

Current model: `CORE_V2_7_2026_09`. User-approved change on 7 September 2026: pair signals and new pair snapshots require all five CORE factors (100% coverage) on both sides. Currency research retains the 50% availability gate. Factor formulas and weights (35/20/20/20/5) are unchanged. Existing V2.6 and older snapshots remain untouched. Access keys do not certify data accuracy. Missing, future-dated and stale observations must remain unavailable. Coverage is not a probability of success.

## Verified changes, 7 September 2026

- EUR 2Y: Bundesbank `BBSSY.D.REN.EUR.A610.000000WT0202.A`, daily yield of current two-year Federal Treasury notes. Explicitly a German benchmark, not an euro-area aggregate. Keyless official endpoint; validates series, percent unit, dates, flags and conflicting observations.
- CAD 2Y: Bank of Canada Valet `BD.CDN.2YR.DQ.YLD`, daily two-year benchmark. Keyless. The older V39051 identifier did not resolve through Valet; the replacement was discovered in the official series catalogue.
- NZD CPI: Stats NZ quarterly release, all-groups annual percentage change. Keyless. The parser verifies the release title, quarter, annual series and actual publication timestamp. It never verifies static local numbers by pinging an unrelated API. It only returns the retrieved release, not fabricated historical vintages or index levels.
- FCS: existing key works with `symbol=EURUSD`, `period=1D`. Reads v4 dictionary candles and checks actual ticker/profile metadata, daily interval and valid OHLC. Retains provider timestamps; does not move candle dates to force snapshot eligibility.
- e-Stat requests now use HTTPS.
- Disabled news requests remain disabled. An absent Benzinga result no longer appears as fresh demo data.

The existing EODHD cache remains a fallback for yields. GBOND and Economic Events require endpoint entitlement according to EODHD documentation; a free daily call allowance does not establish entitlement. A September 7 test of the existing key returned HTTP 403 for GBOND. No extra keys or accounts were created to circumvent an exhausted quota.

## Existing credential checks

The legacy local project contains configured credentials. Values were never added to this repository or printed in test results. FRED, Stats NZ and e-Stat requests succeeded; the corrected FCS request succeeded. These checks certify only the tested endpoint at the time of the test. Other provider credentials have not all been validated. Avoid registering additional news services: they are outside live CORE.

GitHub Actions secrets and Streamlit secrets are separate. The September 6 collector reported EODHD_API_KEY missing in Actions even though a key exists locally. The connected GitHub tool cannot manage Actions secrets. Do not assume that this patch copies local credentials to either host.

## Protected operator functions

COT writes, global cache clearing, forced policy refresh and manual policy overrides require an operator password. Normal visitors can read and analyse data without one. Automated collection continues independently.

To enable operator access, configure `DASHBOARD_OPERATOR_PASSWORD` as a long random secret (minimum 16 characters) in Streamlit Settings → Secrets. Never commit its value. The sidebar password is compared with the server secret for each protected action. Leave it unset to keep all public editing locked. Local manual COT files are not a durable multi-instance database.

## Remaining work

- Verify and connect the remaining genuine 2Y sources for GBP, JPY, CHF, AUD and NZD without substituting other maturities or silently switching to a different yield definition.
- Repair missing labour/GDP series with documented official equivalents.
- Resolve automated RBNZ verification and provider release changes.
- Validate PMI source consistency and applicable redistribution terms.
- Reduce redundant source calculations during startup.
- Retain invalid historical snapshots as invalid; prospective outcomes need genuine eligible price observations. No historical success rate is inferred from the 306 invalid legacy entries.

## Source documentation

- https://www.bundesbank.de/en/statistics/overview-of-the-statistical-series/-/3-yields-of-current-federal-securities-914558
- https://www.bankofcanada.ca/valet/docs/
- https://www.bankofcanada.ca/valet/lists/series/json
- https://www.stats.govt.nz/information-releases/consumers-price-index-june-2026-quarter/
- https://fcsapi.com/document/forex-api
- https://eodhd.com/financial-apis/macroeconomic-data-api
- https://eodhd.com/financial-apis/economic-events-data-api
