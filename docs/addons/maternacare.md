# MaternaCare

**Status:** pilot, specialist phase. Consent revocation now stops clinical changes. Reminders never replace clinical escalation.

Coordinates pregnancy and postnatal care, and sends every worry to a named person.
Code: `backend/maternacare/`, `web/js/care/materna.js`. Tests: `test_maternacare.py`.

**Later-phase module.** The workflow exists, and it is empty of clinical protocol on
purpose. It needs an obstetric partner before any real use.

## Workflow

1. A clinician starts an episode with `maternal_care` consent, an escalation contact,
   and the team's own response time for concerns.
2. The clinician confirms the due date and plans each visit.
3. The patient sees visits and reviewed education, and keeps a birth plan.
4. A concern creates a high-priority task with a deadline and tells the provider. If
   nobody responds in time, the whole care team is alerted.
5. Delivery moves the episode to postnatal, with an optional linked newborn record.

States: `antenatal`, `postnatal`, `closed`. Closing requires every concern answered.

## Data

`maternal_episodes`, `maternal_visits`, `maternal_risks`, `maternal_education`,
`birth_plans`, `delivery_records`, `maternal_concerns`, and `observations` with
category `maternal`.

## API

Under `/api/addons/maternacare`: `POST episodes`, `GET patients/{id}/episode`,
`POST episodes/{id}/due-date`, `/visits`, `/observations`, `/risks`, `/education`,
`/delivery`, `/concerns`, `/close`, `PUT episodes/{id}/birth-plan`, `PATCH visits/{id}`,
`POST concerns/{id}/respond`.

## Safety

- No AI, by test. Gestation is arithmetic on a clinician-confirmed due date.
- No built-in schedule, danger signs or risk score. These must come from an approved
  protocol, and the code does not invent them.
- Concerns aren't classified. A read-only caregiver can still raise one.
- A missed visit creates a follow-up task for the care provider.
- Patient-entered values stay separate from clinician-confirmed ones.

## Limits

Reminders reach people without the app through staff calls. SMS and voice aren't connected,
and escalation still relies on staff acting on tasks. Education is per episode with no shared
library, and it isn't checked against national programmes. Offline use isn't supported.
