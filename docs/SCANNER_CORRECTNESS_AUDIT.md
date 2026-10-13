# Scanner correctness investigation — 2026-10-13

## Scope and durable baseline

User rejects the apparent three-company result as implausible and requests a source/rule audit, not arbitrary loosening to obtain 50–60 names. Audit both false passes and false rejections. Preserve local Python, accounts, read-only broker protections, quotes, historical recording and seven-day evidence cache. Do not claim completion based only on passing unit tests or finishing requests.

Baseline: `6e7700f`. Earlier entire-universe audit (503 names, moderate preset, 271.282 seconds): 492 rejected, 3 qualified, 8 acquiring. This run is not evidence of screening correctness. Full JSONL: https://www.genspark.ai/api/files/s/bxksDVAY . It contains details for rejected names and counts missing criteria in rejected companies.

User's new pasted export: https://www.genspark.ai/api/files/s/82DIvEGt . Filename `pasted-text-1791899714298.txt`, 379441 bytes. It contains one detailed JSON object (`result`, `evidence`) for FIX, with **optimistic** preset, plus summary-only DECK and TPR entries. It contains no META/AAPL record. Do not represent it as a complete export or compare its preset/count directly with the moderate audit.

## Confirmed FIX false pass: receivables are the wrong accounting scope

CIK 1035983. The export reports revenue growth 196.12%, receivables growth 99.09%, and PASS. Its five AR values come from MarketBeat's generic `Accounts Receivable` label:

| Fiscal end | Export's supposed AR (USD) |
| --- | ---: |
| 2021-12-31 | 61,881,000 |
| 2022-12-31 | 1,101,112,000 |
| 2023-12-31 | 1,391,700,000 |
| 2024-12-31 | 95,786,000 |
| 2025-12-31 | 123,197,000 |

The series mixes subtotals. The 2021, 2024 and 2025 values are **unbilled receivables**, not all trade receivables. This is a real data-mapping defect, not a missing-data excuse.

Primary sources retrieved and examined:
- https://data.sec.gov/api/xbrl/companyfacts/CIK0001035983.json
- https://www.sec.gov/Archives/edgar/data/1035983/000110465926017530/fix-20251231x10k.htm

The actual 2025 consolidated balance sheet states:
- Billed accounts receivable, **less allowance** of $19,708 thousand: $2,577,858 thousand.
- Unbilled accounts receivable, **less allowance** of $1,508 thousand: $123,197 thousand.
- Other receivables, less allowance $325 thousand: $116,157 thousand (separate, nontrade).
- Retainage is separately disclosed in notes, but must not be added again to billed balances.
- Costs and estimated earnings in excess of billings are separately reported contract assets, not an automatic AR add-on.

SEC facts:
- `BilledContractReceivables`: 2021 = 773,716,000; 2025 = 2,577,858,000.
- `UnbilledContractsReceivable`: 2021 = 61,881,000; 2025 = 123,197,000.
- `NontradeReceivablesCurrent`: 2021 = 57,491,000; 2025 = 116,157,000 (exclude from trade AR).

Corrected reported trade AR = billed + unbilled; **do not subtract the already-disclosed allowances again**:
- 2021: 835,597,000.
- 2025: 2,701,055,000.
- Growth: **223.2485276994%**.
- Revenue: 3,073,636,000 → 9,101,641,000; growth **196.1196771511%**.
- Under the current revenue-grows-faster-than-AR criterion, the corrected FIX observation **fails**, reversing the exported PASS.

The intended fix is a CIK-scoped adapter for this verified issuer layout, with all input observations/source dates retained and the provider subtotal preserved as an audit alternative. Do not apply FIX's net/gross interpretation indiscriminately to other issuers.

Other FIX inputs still require reconciliation: exported current ROIC 101.92%, annual average 30.41%; debt/EBITDA 0.02695248. The 2025 filing separately lists current long-term debt 6,163 thousand, noncurrent debt 139,063 thousand and lease liabilities 302,590 thousand. High ROIC alone is not proof of error. Historical/current provider debt definitions may differ, especially operating leases.

## DECK: verified findings versus open work

The new user upload provides only DECK's QUALIFIED summary, proposed IV 178.94 and reference price 85.63. There is no detailed DECK financial evidence in that upload.

Prior live acquisition found GICS `Footwear` contains DECK without another exact-subindustry S&P peer, while NKE is classified as `Apparel, Accessories & Luxury Goods`. Finviz's explicitly labelled DECK peers included ONON, NKE, BIRK, CROX, WWW, RCKY, LULU, VFC, UAA, SHOO. Of these, NKE and LULU were in the acquired S&P universe. The implemented singleton-group fallback preserves the constituent universe and records its source; it is a **provider comparison proxy**, not verified exhaustive direct competition.

Observed comparison snapshot in this investigation (not immutable financial truth): DECK gross/net margins 56.29%/18.42%; NKE 43.34%/6.74%; LULU 56.04%/12.78%. That specific peer check passed. This does **not** validate DECK's full qualification, valuation, history or debt scope. Reacquire and audit each before making that claim.

## META, AAPL and implausibly sparse qualification

From the prior saved moderate scan, not the user’s optimistic FIX export:
- META was rejected by ROE and ROIC trend/average tests. Its calculated current ROE was 26.07% against 27.50% annual mean; calculated ROIC 18.89% against 25.89% annual mean. These were not failures of the 12% threshold. Audit year-end versus average capital, debt/lease definitions, TTM periods and effects of one-time tax items.
- AAPL was rejected by ROE trend/average and revenue-versus-receivables, with debt incomplete. The actual AR annual series used 26,278 million (2021) → 39,777 million (2025), while revenue was 365,817 million → 416,161 million. Verify comparable credit-sales context and the course's intended period/interpretation rather than hard-coding Apple to pass.
- MSFT gross-margin rejection was triggered by 67.944% latest versus 68.771% mean and fitted slope -0.101 pp/year, barely below the invented -0.1 tolerance.
- ADBE net margin annual values approximately 30.55, 27.01, 27.97, 25.85, 30.00%; operating margins 36.76, 34.64, 34.26, 31.35, 36.63%. Despite recovering to roughly the initial level, fitted slope can reject it. Check whether the app's numerical definition of “consistent” is too rigid relative to the actual course.
- Across 221 detailed rows, failure counts included AR 108, ROE 102, ROIC 90, debt 59, OCF 53, net/operating margin 53, earnings 49, gross margin 48, CCC 40. These counts diagnose the funnel; they are not a reason to tune to a target count.

## Code/rule defects and audit priorities

1. Annual balance acquisition filters on form=10-K but not fiscal-year-end. A 10-K may include quarter-end equity/other instants. Filter fiscal ends **before** the 12-observation limit. Existing test merely searched Python source text for an `if` clause; replace with behavioral quarterly-heavy history test.
2. FIX AR scope above is confirmed and must have a regression test that reverses the false pass.
3. ROIC historical debt is preferentially taken from Yahoo TotalDebt, while current debt may remain MarketBeat's different scope. Standardize interest-bearing debt/finance leases versus operating leases consistently, keep competing definitions visible, and do not divide by a mixed-definition historical denominator.
4. Course debt rules: master document lines 2480–2500 explicitly say passing 2/3 is acceptable. Current moderate uses 3/3. Review preset intent and preserve clearly labelled genuinely stricter presets rather than silently treating added constraints as course requirements.
5. Course “consistent or rising” and current approximately at/above average do not prescribe all existing hard slope cutoffs. Audit rather than simply remove all gates.
6. Operating-margin fallback must carry through to peer comparison; comparing substituted operating performance against competitors' net margins is not like-for-like.
7. Provider-selected largest GICS peers are not necessarily “closest competitors.” Retain explicit selection provenance and audit the competition rule.
8. Trace all-company acquisition gaps, including issuer filings/registration historical carve-outs, without assuming a parent's unrelated consolidated history belongs to a new spin-off.

## Fresh follow-up acquisition and cache migration

After `8670ef6`, fresh full-pipeline acquisitions for FIX, DECK, META and AAPL were saved losslessly (including calculation inputs, source alternatives and all preset evaluations): https://www.genspark.ai/api/files/s/IrfBOMwI . This is an audit dataset, not approval of all verdicts.

- FIX now correctly FAILS AR: 223.25% versus 196.12% sales growth, with all five annual balances recovered from billed + unbilled SEC facts.
- DECK still appeared QUALIFIED, but its CCC was incorrectly N/A because the physical-goods industry matcher omitted `Footwear`. Actual acquired annual CCC days: 56.7559, 75.6268, 60.2866, 38.0825, 35.9128 (FY2022–FY2026). It must be assessed, not exempted.
- DECK mixes MarketBeat debt of zero with Yahoo annual debt of 246.488m, 266.879m, 276.975m and 375.194m. Reconcile leases before treating the ROIC mean/current comparison as consistent. Current ROIC 138.59% is not independently verified by this acquisition.
- FIX likewise mixes Yahoo 2025 debt 483.358m with current MarketBeat debt 54.064m. Debt/EBITDA and ROIC require scope normalization.
- META and AAPL fresh rejection categories reproduce the earlier audit; neither is whitelisted.

A separate calculation revision now invalidates legacy cached **verdicts**, not the browser evidence store. Legacy rows are withheld on restore, then POSTed for supplementation and full preset recalculation on Scan; original collection dates are enforced. Old FIX subtotals and non-fiscal-year balance instants are quarantined before merge, so a failed source retry cannot resurrect them. Successful HTML/Yahoo acquisition markers survive. Node tests cover successful migration, failed migration and expiry protection; Python tests cover invalid-observation quarantine and immutability. Focused checks: 45 Python tests and 17 scanner JavaScript tests pass; embedded frontend rebuilt.

## Reset recovery and completion discipline

Every new interrupted turn has restored the sandbox to the latest pushed commit; local dependencies, uncommitted edits and logs were repeatedly lost. **Commit/push tested small changes immediately.** Read current git status and implementation before restoring anything.

At creation of this note the FIX/annual-balance edits had been lost before push. The next continuation confirmed a clean tree at `556d743` and restored both corrections. Fiscal-end filtering now precedes the twelve-observation cap. The FIX adapter uses only matched billed + unbilled filed observations; incomplete full-scope history cannot be filled with known ambiguous provider subtotals. Displaced values remain in diagnostic alternatives. Behavioral tests replace the brittle source-text assertion, exercise twelve fiscal years with quarter instants inside 10-K filings, and demonstrate the FIX AR FAIL (223.25% versus 196.12%). `python -m unittest test_vmi_sp500 test_vmi_data`: 44 passed after restoring pinned dependencies. This is a regression checkpoint, not live financial or full-universe correctness approval. Cache migration, live verification and the remaining accounting/rule audit are still required.
