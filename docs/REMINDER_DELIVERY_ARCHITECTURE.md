# Reminder delivery architecture

Code: `backend/reminders/`. Tests: `test_reminders.py`. Screen: Reminders.

A reminder records what actually happened. If it can't be confirmed, a person follows up.
**A reminder is not a clinical escalation,**.

## Flow

1. A clinician on the care team schedules a reminder, optionally linked to a task.
2. When due, the system checks preferences, consent and which channels exist.
3. It tries in-app first if the patient has an account, then any configured SMS or voice.
4. If nothing can reach the patient, it creates a staff task and adds them to the call list.
5. No confirmation in time also creates a staff task.
6. Staff record the outcome. Each step is audited.

## States

| State | Means | Does it mean received? |
|---|---|---|
| `pending` | Scheduled | No |
| `in_app_sent` | Shown in the app | No |
| `external_delivery_pending` | Handed to a provider | No |
| `delivered` | **Only** from a provider's report | Reached the handset, not read |
| `failed` | Provider refused or lost it | No |
| `staff_followup_required` | A person must act | No |
| `acknowledged` | Patient confirmed, or staff reached them | Yes |
| `cancelled` | Withdrawn | No |

## Channels

| Channel | Status |
|---|---|
| In-app | Working |
| Clinic call and printed list | Working |
| SMS, voice | **Not connected.** Adapter interface only, no provider in the code |

Using SMS or voice needs all of: `ENABLE_EXTERNAL_MESSAGING=1`, an adapter registered in code,
the patient's `external_messaging` consent, their preference and a phone number. Anything
missing means a person follows up. There is no fallback provider.

Outside carriers only ever receive one fixed line: "Please contact your clinic. This is a
message from your care team."

## Clinic follow-up

The follow-up list shows the clinician's own patients, highest priority first, with reasons,
due times, and attempts. It can be filtered to one tool.

The printable list holds first name, surname initial, phone number, the words "Clinic
follow-up", and attempts. It holds no clinical text and doesn't reveal which programme. Each
use is audited per patient. Paper still needs a local handling rule: the sheet says to destroy
after use, and nothing enforces it.

## Tested

Failure creating a staff task, consent revoked, messaging switch alone sending nothing, a
registered adapter staying unused while messaging is off, neutral outside text, receipts,
timeouts, isolation, audit, and print output in Chrome. Delivery used a fake provider.

## Not tested or built

A real SMS or voice provider, delivery receipts from one, retries, quiet hours, language
choice, or a shared clinic queue across organizations. Follow-up tasks go to the person who
scheduled the reminder, so absence cover needs a procedure.

## Needs people

| Task | Person |
|---|---|
| Choose a provider, sign an agreement, review security | Vendor, legal, security |
| Decide who covers follow-up lists | Clinic lead |
