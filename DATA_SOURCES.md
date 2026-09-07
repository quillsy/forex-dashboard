# Live data release V2.8

Current model: `CORE_V2_8_2026_09`. Pair signals require five qualified factors on both currencies AND an unexpired collector check. Unknown release calendars expire after one hour. Known future release deadlines are represented separately from maximum observation ages. A failed fetch never renews the successful-check timestamp.

## Additional qualification, September 7

The user explicitly approved labelled official provisional releases and the UK official rolling three-month unemployment rate. The live probe now qualifies 25/40 factors (no pair reaches 100% because PMI remains unqualified).

- EUR HICP: current `prc_hicp_minr:M.RCH_A.TOTAL.EA21`, direct annual rate. August 2026 3.2%, official estimated flag `e`, visibly labelled. No private forecast substituted.
- JPY CPI: current 2025-base table `0004052037`, all-Japan all-items direct YoY1.9% July; exact metadata and monthly time-code guards. No old-base fallback.
- AUD CPI: exact ABS `CPI/3.10001.10.50.M`, series unitPCT and all five dimensions validated; July3.5% YoY.
- GBP GDP: ONS IHYR PN2/QNA quarterly real YoY; latestQ2 1.2%. Both publication families checked; conflicting same-release values block.
- GBP labour: ONS MGSX/LMS, 4.9% April–June rolling three-month rate. Never represented as a standalone May monthly reading.

Known ONS release calendar dates are retained as dates, not invented publication hours. Live detail readers use the same normalized cache as the CORE. Provider counters preserve totals across runs where a provider is not queried. RBNZ requests remain disabled until provider automation permission is confirmed. Other unresolved source definitions remain blocked.

Live CPI observations now retain frequency, annual-rate unit and reference period consistently. In particular, Stats NZ CPI is quarterly; its existing 180-day maximum in the source reader is also preserved by the normalized cache, rather than accidentally applying the 90-day monthly limit.

## New verified public adapters

- Eurostat EUR unemployment: `une_rt_m:M.SA.TOTAL.PC_ACT.T.EA21`, monthly, seasonally adjusted labour-force percent. Live probe July 2026: 6.4%.
- Eurostat EUR GDP: `namq_10_gdp:Q.CLV_PCH_SM.SCA.B1GQ.EA21`, quarterly real YoY, seasonal/calendar adjusted. Live probe Q2 2026: 1.2%. Uses period-end dates, not fabricated release dates.
- Japan Ministry of Finance: `jgbcme.csv`, exact 2Y column and percent unit, coupon-bond constant-maturity yield. Explicitly named, no zero-coupon substitution. Live probe September 4: 1.83%.
- Existing FRED sources are additionally validated against exact original series metadata. Real GDP YoY series are not transformed twice; GDPC1 is transformed using the same quarter one year earlier. GDP freshness uses actual quarter end, preserving the original label for the YoY lookup.

## Central collection and public state

`run_data_collection.py --live-only` collects normalized CORE observations. The workflow runs at minute 17 and 47; the existing 22:00 UTC daily work remains separate. Requests are deduplicated within each run. Dashboard HTTP reads are blocked by the transport; the live currency/pair views use `live_core_data.json`. Old raw/history/context panels can be incomplete and are labelled as such. Backtesting algorithms and historic snapshots have not been rewritten.

The public cache includes only an explicit projection of market observations and provenance. Raw provider responses, headers, tokens and request URLs are not persisted. Observations are validated again on read. The site checks freshness even when the scheduler stops. Scheduled jobs can be delayed; this is not a one-hour service guarantee.

Provider telemetry counts requests in the last run and UTC day. Actual account limits, reset times and remaining balances are UNKNOWN unless independently established; local counts must never be advertised as provider-confirmed balances. The operator view is password protected. No outbound alert service is configured.

## Intentional live blocks

- PMI: exact survey consistency and public redistribution rights are not established. All live PMI factors are withheld; consequently no pair currently meets 100%.
- CHF inflation now uses Eurostat `prc_hicp_minr:M.RCH_A.TOTAL.CH`, preserving HICP rather than switching to national CPI. The September 7 source check returned July 2026 0.7% YoY; the empty August slot is excluded. Dataset update time is not treated as an observation publication time.
- Unsupported CHF/NZD FRED unemployment identifiers remain disabled. The user approved official quarterly seasonally adjusted unemployment for both currencies: Stats NZ HLFS total and BFS ILO total, clearly labelled as quarterly. They use 120-day fresh/180-day maximum age from quarter end, with release deadlines taking precedence. NZ's known next release date is enforced conservatively from midnight NZ time because the exact future publication hour is unknown; CHF requires hourly checks while its calendar is unknown. Both adapters discover releases/resources dynamically and reject mismatched metadata. Source links and attribution are shown in the public table.
- GBP/CHF/AUD/NZD genuine compatible 2Y access remains unresolved. RBNZ automated access requires provider permission; the existing failure is not bypassed.
- A zero value, proxy, old release or additional free account never fills one of these gaps.

## Credential security

Embedded defaults have been removed from the old `app_zero_overlap.py`. Historical credential exposures still require provider-side revocation/rotation. Known affected provider hosts are blocked for collection until replacement has been performed and the operator explicitly configures `FX_ROTATED_PROVIDER_HOSTS`. This marker is not proof of rotation; set it only after provider confirmation. No secrets have been rotated or newly issued by this release.

GitHub Actions and Streamlit secrets are separate configurations. Existing local credentials were used only in isolated authorized probes; they were not copied into repository files. `DASHBOARD_OPERATOR_PASSWORD` remains optional and unset unless independently configured.

## Verification evidence

See `CORE_SOURCE_AUDIT.md` for all 40 factor source routes and remaining qualifications. Eurostat attribution is displayed in the source table; CORE scores are our calculations, not Eurostat publications.

- https://ec.europa.eu/eurostat/help/copyright-notice
- https://ec.europa.eu/eurostat/cache/metadata/en/une_rt_m_esms.htm
- https://ec.europa.eu/eurostat/cache/metadata/en/namq_10_gdp_esms.htm
- https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/qa.htm
- https://www.mof.go.jp/english/about_mof/notice/index.html
- https://fred.stlouisfed.org/docs/api/fred/series.html

---

## Prior V2.7 implementation notes (historical)

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
