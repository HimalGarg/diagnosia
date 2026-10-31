# Open-source audit

Checked on 31 October 2026 by shallow-cloning each repository and reading its
licence file and source. This replaces the "observed" column in section 12 of the
blueprint where the two disagree. It is an engineering audit, not legal advice.

## What was reused

| Add-on | Candidate | Commit | Licence found | Outcome |
|---|---|---|---|---|
| ClinScribe | OpenScribe | `f70a8d6` | MIT file | **Code ported.** Note verifier rewritten in Python and extended. Prompt rules adapted. Next.js, Electron, storage and auth not used |
| LabLens | Lab2FHIR | `34c9b4b` | MIT file | **No code used.** See below |
| LabLens | FHIR-XTract | `8e3d434` | MIT file (blueprint: not verified) | **Patterns adapted** for report headers. YAML-driven extractor and 1,891-line FHIR mapper not used |
| ReferralLoop | Healthtech Referral Toolkit | `e5fc18a` | CC BY 4.0 | **Used** for stages and test scenarios TS-01 to TS-08, with attribution |
| MedGuard | RxNav / RxNorm | public API | NLM terms | **Used** for on-request lookups, as suggestions only |
| MedGuard | openFDA labels | public API | Open, attribution | **Used** for on-request label search, as supporting text only |
| DischargeCare | Microsoft Patient Discharge Planning | `142c4da` | MIT file | **Ideas only.** It is a Power Platform solution with no reusable Python or JS. Barriers, flags and the huddle board were rebuilt |

## What was not used, and why

| Candidate | Finding | Decision |
|---|---|---|
| MedRecon (`c9fd62f`) | README says MIT, but there is no `LICENSE` file. Its interaction data is the part we would want | Nothing copied. Not reopened until the maintainer adds a licence. Starter rules were written separately |
| Chronicly | The clone returned "repository not found" | Dropped. ChronicCare is native |
| AHRQ eCare Plan | Reference material | Shared-plan and patient-entered-data ideas only. No code |
| Foundation AMR Guard (`d0faa82`) | AGPL-3.0 TypeScript prototype whose purpose is antibiotic recommendation | Nothing copied, for licence reasons and because the blueprint forbids this behaviour |
| AMR R package | GPL-2.0, in R | Not embedded. Worth considering as a separate check on our figures |
| OpenMRS, Agni | Whole platforms | Not deployed. A second patient database is the wrong trade |

## Findings that differ from the blueprint

- **Lab2FHIR is not the "best technical starting point".** Its parsers read Chinese
  pathology, HPV and cytology reports, keyed on Chinese text, and its LOINC table has
  four report-level rows. It has no analyte-level extraction such as a blood count,
  and its generator hard-codes a Shanghai pathology centre. Its useful idea, reading
  with `pdfplumber`, is a one-liner. LabLens therefore has its own analyte catalogue
  and line parser.
- **FHIR-XTract now has an MIT licence**, so its patterns could be used.
- **OpenScribe's verifier had gaps, fixed in the port.** Its number pattern skips
  values joined to a unit ("500mg"). "No" and "denies" are stop words, so "no chest
  pain" matches "chest pain". There are no checks for side of body or units, and a
  Hindi transcript would make every line look unsupported.
- **The blueprint assumed SQLAlchemy and an existing DiagnoFast backend.** The code
  uses raw `sqlite3`, and DiagnoFast has no backend yet, so there is no shared
  intake encounter to link to.

## Before reopening any of these

Pin the commit, read the licence and dependency tree, and record the outcome here.
