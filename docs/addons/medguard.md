# MedGuard

**Status:** pilot. **No approved rules, so no interaction checks run.** Rule workflow in `../CLINICAL_RULE_REVIEW_WORKFLOW.md`.

Compares medication sources and raises questions for a clinician or pharmacist.
Code: `backend/medguard/`, `web/js/care/medguard.js`. Tests: `test_medguard.py`.

## Workflow

1. Medicines are added by clinicians, patients, or from a prescription the patient
   confirmed in the Prescription Analyzer. Each source is kept as written.
2. Names are matched to ingredients. Matches must be exact, and "Dolo Cold" is never
   treated as "Dolo".
3. The engine flags unidentified medicines, allergies, possible interactions, shared
   ingredients, same-class medicines, strength or schedule conflicts, omissions and
   past end dates.
4. A clinician reviews alerts, with a reason, and records a decision for each medicine.
5. Finalising creates a separate `reconciled` list. Source records are not changed.
   Undecided medicines are listed as unresolved.

Every high-severity alert must be reviewed before finalising.

## Data

`medication_records`, `allergies`, `medication_reconciliations`, `medication_alerts`
and `terminology_cache`. Content lives in `knowledge/catalog.json` and
`knowledge/interactions.json`, each with a version, jurisdiction, date and owner.

## API

Under `/api/addons/medguard`: `POST patients/{id}/medications`,
`POST patients/{id}/import-prescription`, `PATCH medications/{id}`, `POST reconcile`,
`POST check-interactions`, `POST alerts/{id}/review`,
`POST reconciliations/{id}/finalise`, `GET patients/{id}/reconciliations`,
`GET knowledge`, `GET lookup`, `GET label-evidence`.

## Safety

- No AI. The rules are plain code and data.
- Only approved rules alert. Each says "potential", and names its rule, reviewer and evidence.
- MedGuard never changes a medicine or says to.
- RxNorm and openFDA are US sources, shown as suggestions for a person.

## Limits

The 21 starter rules are unreviewed and raise no clinical alerts. The brand list is about
55 names and is also unreviewed. There are no dose checks, and no
drug-condition, renal, hepatic, paediatric or pregnancy logic. A licensed, maintained
India drug database is the real next step.
