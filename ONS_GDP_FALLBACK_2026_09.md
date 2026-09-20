# ONS IHYR transport fallback

The GBP GDP factor remains ONS IHYR: quarterly real GDP growth versus the same quarter one year earlier, seasonally adjusted, percent. PN2 and QNA publication families remain required; newer releases and same-release conflicts are handled by the existing parser/selection logic. No CORE weights, version or freshness limits change.

Only the tested PN2 alternative is enabled:
`https://api.beta.ons.gov.uk/v1/data?uri=/economy/grossdomesticproductgdp/timeseries/ihyr/pn2`

The website remains primary. One additional request is permitted only after timeout, connection failure (excluding TLS errors), or HTTP500/502/504. No fallback after authentication/authorization errors, rate limits,503, provider cooldown, malformed payloads or invalid metadata. QNA keeps its original route and remains required; this is not complete ONS-outage redundancy. Both routes belong to ONS, not independent measurement providers.

Selected observations preserve the actual fallback URL through an exact public allowlist; arbitrary query parameters remain excluded. Transport exceptions preserve sanitized transient types without carrying raw requests, response bodies or credentials. Publication dates, reference periods and hourly checks are unchanged.

## Evidence and attribution

Independent production-parser check20September2026 14:41UTC: both PN2 routes HTTP200,282quarters, identical SHA256 `9863b4f9ca9283bec02e59965fac63877ed50251c568a617a8fa85e95ce2b171`; latest2026-Q2 value1.2%. This is point-in-time equivalence, not an uptime guarantee.

Source: Office for National Statistics. Contains public sector information licensed under the Open Government Licence v3.0. No ONS endorsement is implied.

- Documented v1/data migration: https://developer.ons.gov.uk/retirement/v0api/
- ONS reuse terms: https://www.ons.gov.uk/help/terms-conditions
- Licence: https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/

Only normalized permitted observations are published. No private research bundle or unrelated candidate data is included.
