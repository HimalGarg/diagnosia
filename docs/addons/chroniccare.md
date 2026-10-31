# ChronicCare

**Status:** pilot. A CDSCO device candidate: see `../CLINICAL_GOVERNANCE.md`. Reminders now go through `../REMINDER_DELIVERY_ARCHITECTURE.md`.

Follows long-term conditions between visits. It doesn't diagnose or change treatment.
Code: `backend/chroniccare/`, `web/js/care/chroniccare.js`. Tests: `test_chroniccare.py`.

## Workflow

1. A clinician records a condition and writes a plan with goals and measures.
2. Approving makes it visible to the patient and activates its limits. A newer plan
   retires the old one.
3. The patient logs readings, symptoms and doses taken, missed or held.
4. The dashboard shows trends, overdue measures, adherence and confirmed lab results.
5. A reading past a limit shows the clinician's instructions, and creates one
   high-priority task per measure per day.

## Data

`condition_profiles`, `care_plans`, `care_plan_goals`, `care_plan_measures`,
`adherence_events`, and `observations` with categories `vital` and `symptom`.

## API

Under `/api/addons/chroniccare`: `GET measures`, `POST patients/{id}/conditions`,
`POST patients/{id}/plans`, `POST plans/{id}/approve`, `POST patients/{id}/readings`,
`POST patients/{id}/symptoms`, `POST patients/{id}/adherence`, `GET patients/{id}/dashboard`.

## Safety

- Home readings are labelled patient-entered and stay `pending` review.
- Alerts exist only if a clinician set a limit, and a limit requires instructions.
- Typing limits catch mistakes such as 140% oxygen. They say nothing about health.
- Adherence comes only from what was reported, never from a prescription.
- Attention messages say it isn't an emergency service.

## Limits

The UI's plan form holds one measure at a time, though the API takes several. Nothing
reviews a patient-entered reading in bulk yet. There are no appointment reminders,
disease templates or device integrations.
