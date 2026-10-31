# DischargeCare

**Status:** pilot. Reminder delivery is in-app or by staff call only.

Turns a discharge plan into a checklist and tracked tasks. It doesn't decide discharge.
Code: `backend/dischargecare/`, `web/js/care/discharge.js`. Tests: `test_dischargecare.py`.

## Workflow

1. A clinician starts an episode as its responsible clinician.
2. They add the summary, emergency contact, instructions, barriers, pending results
   and tasks. Tasks are held until approval.
3. The responsible clinician approves. Tasks open and the patient is told.
4. The patient or caregiver sees the checklist. The team works from the board.
5. Closing requires required tasks done, and pending results final, or a written
   exception.

States: `draft`, `active`, `closed`.

## Data

`discharge_episodes`, `discharge_instructions`, `discharge_barriers`,
`discharge_pending_results`, and shared `tasks` linked to the episode.

## API

Under `/api/addons/dischargecare`: `POST episodes`, `GET board`,
`GET patients/{id}/episodes`, `GET`/`PATCH episodes/{id}`,
`POST episodes/{id}/instructions`, `/barriers`, `/pending-results`, `/tasks`,
`/approve`, `/close`, `GET episodes/{id}/checklist`, `POST barriers/{id}/resolve`,
`POST pending-results/{id}/finalise`.

## Safety

- Nothing reaches the patient before approval, and no AI writes instructions.
- The approved summary and instructions are fixed.
- Pending results are shown as pending.
- Overdue high-priority tasks escalate to the responsible clinician, once.

## Limits

SMS and voice aren't connected, so phone follow-up remains essential. Hindi instructions
must be written by hand. There is no bed-management or hospital system link, and
follow-up appointments are tasks rather than bookings.
