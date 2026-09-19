# August 2026 EA21 HICP conflict resolution

Verified 19 September 2026, 11:33 UTC. This changes availability evidence, not CORE scoring or weights.

The full Eurostat release of 17 September reports EA21 August annual all-items HICP at 3.2%. It supersedes the August flash estimate of 3.3% for the current observation:
https://ec.europa.eu/eurostat/web/products-euro-indicators/w/2-17092026-ap

Direct Statistics API query, `prc_hicp_minr`, `freq=M`, `unit=RCH_A`, `coicop18=TOTAL`, `geo=EA21`, returned August 3.2 with no provisional/estimate flag. API update timestamp: `2026-09-17T11:00:00+0200`. This is a database update timestamp, not a separately established publication timestamp.

July remains in conflict: the same API returns 3.0 while the new release reports 2.9. Its block is retained. The current CORE inflation score uses the selected latest annual rate; this resolution does not authorize use of conflicting July data for trends or historical analysis.

The August exception requires the exact series, source, unit, adjustment, reference month, unflagged non-estimate value and a successful check at or after the verification timestamp. Old cached records and different values remain blocked. Normal expiry and hourly checks still apply. Historical snapshots retain their recorded state.

The release's next full-data date is 16 October; this is not substituted for the earlier flash-release schedule. No new release calendar is inferred, so hourly verification remains required.
