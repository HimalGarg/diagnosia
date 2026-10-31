# Clinical rule review workflow

Covers MedGuard rules and LOINC mappings. Screen: Governance. Tests:
`test_clinical_governance.py`. The software provides the workflow. All clinical decisions
belong to the named reviewers, and **no rule or mapping has been approved**.

## MedGuard rules

Each rule stores its ID, category, medication identifiers, description, evidence source,
version and date, creation date, last review date, reviewer, status and rule-set version.
History is append-only.

| State | Meaning | Clinical alert? |
|---|---|---|
| `draft` | Written, no review. All 21 starter rules start here | No |
| `pending_pharmacist_review` | Evidence added, submitted | No |
| `approved` | A verified pharmacist approved it | **Yes** |
| `rejected` | Refused, can be fixed and resubmitted | No |
| `retired` | Withdrawn | No |

Steps: a verified pharmacist adds evidence, then submits. A **different** verified pharmacist
approves or rejects with a reason. Approved rules can't be edited: retire one and write a
replacement.

**The 21 starter rules** were written by the project without a pharmacist. They have no
evidence recorded, and none was invented. They are kept for review, not as a validated set.
A pharmacist may find them wrong, incomplete, or better replaced by a licensed database.

## What a clinician sees

| Situation | Shown |
|---|---|
| Approved rule matched | Alert with rule, reviewer and evidence. Needs a review before finalising |
| Unverified rule matched | "Unverified, not a clinical alert". The content is held back |
| No approved rule matched | Says the set is limited and that this tells you nothing about suitability |
| No rules approved | Says the medicines have not been checked against each other |
| Lookup failed | Says RxNorm could not be reached |
| Medicine unidentified | Says no rule checked it |

Reassuring wording is blocked by tests on the engine output and on the screen's code.

`ENABLE_UNVALIDATED_CLINICAL_RULES=1` shows unreviewed rule text, labelled, for synthetic
pilot testing only. It never raises severity, and it is ignored in production or with real
data, and stops a production start.

List comparisons, such as a shared ingredient or a recorded allergy, need no rule and stay on.
They rely on the unreviewed drug list and say so. Removing them would remove a safeguard.

## External references

RxNorm and openFDA stay suggestions. Results carry their source and retrieval time, are
validated, expire after 30 days, and a failure is never cached. A label search is labelled
as not being a complete interaction check.

## LOINC mappings

58 proposals were seeded from the project's own catalogue. All pass LOINC's check digit,
which catches typing errors only, not a wrong concept. **None was checked against a LOINC
release.**

| State | Exported as a code? |
|---|---|
| `proposed` | No. The laboratory's own name is exported as text |
| `approved` | Yes |
| `rejected` | No |

A verified laboratory professional approves against a named LOINC release, rejects, or
corrects. A correction becomes a new proposal, which someone else must approve. Each step is
kept in `loinc_mapping_events`. Codes are never guessed for an unknown test.

## AMR Guard

Its clinical content stays empty on purpose. The API reports decision support as `unavailable`.
Its organism and antibiotic list only validates imports and isn't a formulary. No route or
code path recommends, substitutes or doses, and the package imports no AI or network client,
enforced by tests.

## Who must act

| Task | Person |
|---|---|
| Review or replace medication rules, and the brand and class list | Pharmacist |
| Review mappings and plausibility limits | Laboratory professional |
| Review antibiogram method and vocabulary | Microbiologist, stewardship lead |
