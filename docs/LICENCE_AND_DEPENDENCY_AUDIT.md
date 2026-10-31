# Licence and dependency audit

Diagnosia is Apache-2.0. Not legal advice: have counsel confirm before distribution.

## Third-party code in this repository

| Source | Licence | Where | Obligation | Status |
|---|---|---|---|---|
| OpenScribe | MIT | `backend/clinscribe/verifier.py`, `drafting.py` | Keep the notice | Done in `THIRD_PARTY_NOTICES.md` |
| FHIR-XTract | MIT | `backend/lablens/parser.py` | Keep the notice | Done |
| Referral Toolkit | CC BY 4.0 | `backend/referralloop/routes.py`, its tests | Attribute and state changes | Done |

No code from any GPL, AGPL, MPL or unlicensed source is in this repository.

## New dependencies

| Package | Licence | Scope |
|---|---|---|
| RapidFuzz | MIT | Runtime |
| pdfplumber, pdfminer.six | MIT | Runtime |
| cryptography | Apache-2.0 or BSD-3-Clause | Runtime |
| pypdfium2 | BSD-3-Clause, Apache-2.0, bundled PDFium licences | Runtime |
| reportlab | BSD | Tests only |

## Data and services

| Item | Terms to respect |
|---|---|
| LOINC codes in `backend/lablens/analytes.json` and `chroniccare/routes.py` | The licence requires its notice. **Not yet added.** See `CLINICAL_GOVERNANCE.md` |
| RxNorm through RxNav | NLM attribution, shown in MedGuard. 20 requests per second limit, and only clinician lookups call it. Results are cached |
| openFDA | Rate-limited without a key. Called only on request |
| Groq | Patient data leaves our servers and India. Check its terms and sign an agreement before real use |

## Our own content, not third-party

`backend/medguard/knowledge/*.json`, `backend/lablens/analytes.json` and
`backend/amrguard/catalog.json` were written for this project as unreviewed starter
data. Their accuracy needs professional review, covered in `CLINICAL_GOVERNANCE.md`.

## Data and models from the original tools

These aren't in git, and each limits how the product can be used.

| Item | Licence | Effect |
|---|---|---|
| PanDerm code and weights | CC-BY-NC-ND 4.0 | Non-commercial academic research only. Replace before charging |
| ISIC 2019 images | CC-BY-NC | Non-commercial |
| Indian Medicine Dataset | Repository is MIT, but the data looks scraped, so upstream permission is unconfirmed | Fine for a demo. Replace before selling |
