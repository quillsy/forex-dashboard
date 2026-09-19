# BOJ current rate and announced change: 19 September 2026

Verified directly against Bank of Japan documents on 2026-09-19 at approximately 11:41 UTC.

- [Current complementary deposit facility terms](https://www.boj.or.jp/en/mopo/measures/term_cond/yoryo36.htm), section 4: current rate 1.0%, revision dated 16 June 2026.
- [16 June guideline change](https://www.boj.or.jp/en/mopo/mpmdeci/mpr_2026/k260616a.pdf): 1.0% overnight call-rate guideline, effective 17 June. This remains the current rate episode.
- [18 September guideline change](https://www.boj.or.jp/en/mopo/mpmdeci/mpr_2026/k260918a.pdf): announces 1.25%; footnotes 1 and 2 explicitly make the guideline and deposit-facility change effective 24 September 2026.
- [18 September facility amendment](https://www.boj.or.jp/en/mopo/mpmdeci/mpr_2026/mpr260918b.pdf): independently confirms 24 September effectiveness. Its struck-out old/new rate text can extract as `1.01.25`; this amendment is supporting evidence, not a numeric parser input.

The old parser compared the current facility rate with the announced future rate, causing a genuine but explainable mismatch. The corrected parser retains two re-read official proofs of the current 1.0% rate, with the latest announcement stored separately as metadata. The second current proof retains its actual June source URL; the September announcement is `decision_source`.

A date-only effective date uses the conservative start of the Tokyo day: **2026-09-23T15:00:00Z**. At that boundary old evidence fails closed even when source fetching fails. Fresh matching current terms and the effective announcement are required to activate 1.25%. If the terms update early, 1.25% remains blocked before the effective date. Hourly live-data verification remains required and is not extended by the pending-change deadline.

The Tokyo calendar is used only for JPY policy date validation. No weights, scoring rules, model version, or 100% pair gate are changed.

Episode lookup reads at most the current and two preceding official year indexes, stopping as soon as the current episode is proven. This supports a January hold whose still-current rate started the previous year without replacing the latest decision with the older episode document. Failure to find an episode within that bound remains a visible source-verification failure.

The pre-existing case before the first decision in a new year's index remains conservatively unavailable. This correction does not establish guaranteed perpetual availability.

Validation: 73 policy/live-data regression tests passed, including January hold and bounded historical lookup; a direct read-only invocation of the production parser against BOJ returned the current 1.0% proofs, June episode date, September announcement, and exact expiry shown above. This source verification alone does not prove deployment or a completed production collector run.
