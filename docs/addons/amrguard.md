# AMR Guard

**Status:** pilot, specialist phase. Clinical decision support is `unavailable`. Open only to verified organizations.

Stewardship and surveillance for a hospital's own team. Never for choosing an antibiotic.
Code: `backend/amrguard/`, `web/js/care/amr.js`. Tests: `test_amrguard.py`.

**Specialist-phase module.** It needs a hospital microbiology and stewardship partner.

## Workflow

1. Staff import a CSV of susceptibility results. Bad rows are rejected with reasons,
   and good rows are kept. Repeat imports add nothing.
2. A steward builds an antibiogram for a period, as a draft with a data-quality report.
3. A different steward or microbiologist approves it.
4. Positive cultures move through `positive`, `reviewed`, `acknowledged`, `actioned`.
5. Stewards record reviews: finding, their own recommendation, rationale and outcome.
6. Guidelines are registered with a version and a clinical owner.

## Method

First isolate per patient, per organism, per period. Percent susceptible is
S / (S + I + R). Organisms or drugs with fewer than 30 isolates get no figure, and the
minimum can't be lowered. Mixed standard versions raise a warning.

## Data

`amr_import_batches`, `microbiology_results`, `antibiogram_runs`, `culture_reviews`,
`stewardship_reviews`, `amr_guidelines`. `patient_ref` is the hospital's own reference
and never appears in aggregate output.

## API

Under `/api/addons/amrguard`: `GET catalog`, and under `organizations/{id}`: `imports`,
`antibiograms`, `trends`, `culture-queue`, `stewardship-reviews`, `guidelines`. Also
`POST antibiograms/{id}/approve`, `POST culture-reviews/{id}/advance`,
`PATCH stewardship-reviews/{id}`.

## Safety

- Open only to an organization's admin, steward and microbiologist roles.
- No AI, no recommendation route, no dosing, and tests confirm both.
- Aggregate figures are labelled as not being individual treatment advice.

## Limits

Culture results aren't linked to Diagnosia patients or antibiotic exposure, which is
why the exposure view from the blueprint isn't built. Duplicate rules are the simple
first-isolate rule. There is no WHONET import, and the method needs validation against
current CLSI M39 and NCDC guidance by a microbiologist.
