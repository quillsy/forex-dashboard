# Live CORE source audit — 2026-09-07

This is a source-routing and metadata audit, not a certification that all observations are available, current, or correct. Line references describe the pre-integration `app.py` and may move. The 40-factor matrix below records existing routes; availability must be evaluated by the collector for each run. All five factors must be eligible in each currency before a pair is released.

## Existing 40-factor matrix

`TE` means the Trading Economics public country tables. `Events` means the existing EODHD events cache. All policy-rate inputs require the existing official verification; a two-year yield is a separate required component of Geldpolitik.

| Currency | Geldpolitik (35%) | Inflation (20%) | Labour (20%) | PMI (20%) | GDP (5%) |
|---|---|---|---|---|---|
| USD | Verified policy + FRED DGS2; EODHD US2Y.GBOND fallback | FRED CPIAUCNS with pc1: monthly NSA annual change | FRED UNRATE: monthly SA percent | NAPM manufacturing, TE fallback; TE services; survey identity requires checking | GDPC1 real quarterly level converted using the exact same quarter one year earlier |
| EUR | Verified policy + Bundesbank German benchmark 2Y; DE2Y.GBOND fallback; German instrument explicitly identified | CP0000EZ19M086NEST HICP index to YoY; fixed EA19 geography | LRUNTTTTEZM156S; current metadata/availability requires checking | TE → Events → EUROPAMIMIPDSMEI / EUROPASEIPDSMEI; fallback identities unverified | CLVMEURSCAB1GQEZ fed raw by old code; unit/identity unverified, not approved as direct YoY |
| GBP | Verified policy + UK2Y.GBOND; no keyless equivalent integrated at audit | ONS D7G7 direct annual CPI percent | LRUNTTTTGBM156S; metadata/availability unverified | TE → Events → GBRPAMIMIPDSMEI / GBRPASEIPDSMEI; identities unverified | UKNGDPM fed raw; real/nominal identity and units unverified |
| JPY | Verified policy + JP2Y.GBOND; no keyless equivalent integrated at audit | eStat 2025/2020-base direct annual CPI percent; series metadata not validated in parser | LRUNTTTTJPM156S; current metadata requires checking | TE → Events → JPNPAMIMIPDSMEI / JPNPASEIPDSMEI; identities unverified | JPNGDPRQPSMEI: confirmed real quarterly YoY SA; inspected public page latest Q1 2026 |
| CHF | Verified policy + SW2Y.GBOND; no keyless equivalent integrated at audit | CP0000CHM086NEST: HICP, not national Swiss CPI | LRUNTTTTCHM156S; metadata/availability unverified | TE → Events → CHEPAMIMIPDSMEI / CHEPASEIPDSMEI; identities unverified | CHEGDPRQPSMEI: confirmed real quarterly YoY SA; inspected page latest Q1 2026 |
| CAD | Verified policy + BoC BD.CDN.2YR.DQ.YLD; CA2Y.GBOND fallback | StatCan v41690973 level → exact same-month-prior-year change | LRUNTTTTCAM156S; current metadata requires checking | TE → Events → CANPAMIMIPDSMEI / CANPASEIPDSMEI; identities unverified | CANGDPRQPSMEI: confirmed real quarterly YoY SA; inspected page latest Q2 2026 |
| AUD | Verified policy + AU2Y.GBOND; no keyless equivalent integrated at audit | ABS CPI/3.10001.10.50.M presumed direct annual rate; parser does not validate unit dimensions | LRUNTTTTAUM156S: confirmed monthly SA percent, age 15+; page latest June 2026 | TE → Events → AUSPAMIMIPDSMEI / AUSPASEIPDSMEI; identities unverified | AUSGDPRQPSMEI: confirmed real quarterly YoY SA; inspected page latest Q1 2026 |
| NZD | RBNZ verification incomplete at audit + NZ2Y.GBOND | StatsNZ quarterly release annual CPI rate, actual publication parsed | LRUNTTTTNZM156S: identity/availability unverified; quarterly labour must not be labelled monthly | TE → Events → NZLPAMIMIPDSMEI / NZLPASEIPDSMEI; identities unverified | NZLGDPRQPSMEI: confirmed real quarterly YoY SA |

Source routing: `app.py` 3062–3103 (2Y), 4009–4077 (maps), 4160–4498 (CPI), 4557–4610 (macro), 1705–1810 (live PMI), 4944–5023 (CORE).

## Concrete blockers and validation requirements

- **Publication freshness:** reference period, successful fetch time and publication time are different. Most old CORE records lack checked-at, published-at, next-release and validated source metadata. A successful HTTP response containing old observations does not establish the latest official publication.
- **GDP units:** only GDPC1 is transformed by the old reader (4577). Do not feed EUR/GBP levels directly into percentage scoring. Do not transform the five verified `*GDPRQPSMEI` YoY series again. Verify any replacement's real-price basis, seasonal adjustment and quarter alignment.
- **PMI completeness:** old code (4990–5000) grants the complete factor from either eligible component. Requiring both changes the existing method and must be explicit. Record publisher/survey identity: source fallback must not silently switch ISM to S&P or manufacturing to composite. EODHD event dates (1563) are used as reference periods although they represent event/publication time.
- **Publication timestamps:** ONS fallback month-end+25 (1984/1992), ABS month-end+30 (2120), and eStat third-Friday (2207) are approximations, not official publication metadata. Unknown must remain unknown. StatCan releaseTime loses timezone (2052). Date-only live cutoffs suppress same-day timestamped releases until the next day.
- **Caching:** ONS/StatCan/ABS/eStat/FRED use 86400-second process caches (1941, 2021, 2082, 2149, 2306). They cannot provide an hourly freshness guarantee in a long-lived UI. Central collector status must not refresh merely because a cached function was called again.
- **CPI transformations:** generic row-offset YoY can bridge missing months; match calendar periods and reject incompatible rebasing. EUR EA19 and CHF HICP require accurate labels; switching their economic definition needs explicit treatment.
- **No proxy claim:** MANEMP/BSPRTE mappings are used in context/trend paths (5052/7248), not the current base CORE PMI calculation. Their UI labels still need to distinguish employment/business survey trends from PMI.
- **No universal access promise:** successful existing keys do not guarantee endpoint entitlement, publication rights or perpetual availability. Missing valid equivalents must leave factors and affected pairs blocked.

## Official evidence and usage checks

- [Canada GDP](https://fred.stlouisfed.org/series/CANGDPRQPSMEI), [Japan GDP](https://fred.stlouisfed.org/series/JPNGDPRQPSMEI), [Switzerland GDP](https://fred.stlouisfed.org/series/CHEGDPRQPSMEI), [Australia GDP](https://fred.stlouisfed.org/series/AUSGDPRQPSMEI), [New Zealand GDP](https://fred.stlouisfed.org/series/NZLGDPRQPSMEI): quarterly real same-period-prior-year growth, seasonally adjusted. OECD citation is required; publication calendars were not available on inspected pages.
- [Australia labour](https://fred.stlouisfed.org/series/LRUNTTTTAUM156S): monthly seasonally adjusted percent, population age 15+. OECD source citation required.
- [EUR HICP](https://fred.stlouisfed.org/series/CP0000EZ19M086NEST): EA19 monthly NSA index, rebased to 2025=100; inspected next release September 17, 2026. [European Commission reuse notice](https://commission.europa.eu/legal-notice_en#copyright-notice).
- [FRED metadata API](https://fred.stlouisfed.org/docs/api/fred/series.html) exposes units, frequency, seasonal adjustment, update time and notes; validate these before interpreting observations. Retrieval errors for some exact series pages are not proof a series does not exist.
- [Bundesbank federal-security yields](https://www.bundesbank.de/en/statistics/overview-of-the-statistical-series/-/3-yields-of-current-federal-securities-914558), [BoC Valet documentation](https://www.bankofcanada.ca/valet/docs/), [BoC series catalogue](https://www.bankofcanada.ca/valet/lists/series/json): existing keyless 2Y source evidence.
- [StatsNZ June 2026 CPI release](https://www.stats.govt.nz/information-releases/consumers-price-index-june-2026-quarter/): actual quarter and publication source, not a hardcoded data substitute.
- [S&P PMI FAQ](https://www.spglobal.com/market-intelligence/en/solutions/products/resources/pmi-faq), [EODHD macro endpoint](https://eodhd.com/financial-apis/macroeconomic-data-api), [EODHD events endpoint](https://eodhd.com/financial-apis/economic-events-data-api): public releases and free account status do not establish rights or entitlement to every downloadable data series. Check distribution terms before public payload publication.

No row in this document certifies that all five factors currently meet live signal eligibility. The collector's validated observations, release checks and current eligibility status remain decisive.
