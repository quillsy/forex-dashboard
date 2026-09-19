# Correction: fixed EA21 HICP and changing-composition EA

Verified 19 September 2026. This corrects source comparison and availability evidence; the selected fixed EA21 CORE series, scoring and weights remain unchanged. Historical snapshots retain their recorded state.

## Why the former conflict was invalid

Eurostat distinguishes the official changing-composition euro-area aggregate (`geo=EA`) from stable-composition analytical aggregates such as `geo=EA21`. The news release uses the former. Its geographical note refers to the current 21 member countries but does not make the release identical to the fixed EA21 time series.

Public primary evidence:

- [Eurostat HICP metadata, geographical coverage](https://webgate.ec.europa.eu/eurostat/cache/metadata/en/prc_hicp_esms.htm)
- [HICP Methodological Manual 2024, table 11.4.28](https://ec.europa.eu/eurostat/documents/3859598/18594110/KS-GQ-24-003-EN-N.pdf)
- [17 September 2026 release, geographical information](https://ec.europa.eu/eurostat/web/products-euro-indicators/w/2-17092026-ap)

Direct Statistics API checks used `prc_hicp_minr`, `freq=M`, `unit=RCH_A`, `coicop18=TOTAL`, with each geography requested separately:

| Reference month | Changing composition EA | Fixed EA21 |
| --- | ---: | ---: |
| June 2026 | 2.8% | 2.8% |
| July 2026 | 2.9% | 3.0% |
| August 2026 | 3.2% | 3.2% |

- [EA API request](https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/prc_hicp_minr?freq=M&unit=RCH_A&coicop18=TOTAL&geo=EA&sinceTimePeriod=2026-06&lang=EN)
- [EA21 API request](https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/prc_hicp_minr?freq=M&unit=RCH_A&coicop18=TOTAL&geo=EA21&sinceTimePeriod=2026-06&lang=EN)

Both responses had database update timestamp `2026-09-17T11:00:00+0200` and no observation flags. This is not a separately established publication timestamp. The differing July numbers are not a same-series contradiction. The matching August numbers do not establish equivalence of the two series. The earlier explanation of July as an unresolved official source conflict is superseded by this correction.

## Implementation consequence

Remove the unsupported July/August conflict entries and the August exact-value exception. Valid EA21 observations, including correctly labelled official estimates and revisions, pass the normal parser and record checks without a hardcoded numerical whitelist. Preserve the general same-definition source-conflict mechanism, strict EA21 parser geography, freshness limits, hourly verification when the calendar is unknown, and the 100% pair gate. No switch to changing-composition EA is authorized by this correction.

The release names 2 October as the next September flash date. It does not independently establish the fixed EA21 dissemination schedule, so no EA21 calendar exemption is inferred; hourly successful verification remains required.
