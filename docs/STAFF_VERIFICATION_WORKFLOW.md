# Staff verification workflow

Code: `backend/records/staff.py`, `backend/auth.py`, `backend/operator_cli.py`. Tests:
`test_staff_verification.py`. Screen: Governance.

## Account states

| State | Who | Can do |
|---|---|---|
| `active` | Patients | Their own record |
| `pending_verification` | Every new staff account | Sign in, accept an invitation, fill in a profile. No clinical access |
| `verified` | Staff an administrator checked | Tools, within care teams and roles |
| `rejected` | Failed checks | Sign in only, no staff access |
| `suspended` | Removed | Nothing. All sessions end at once |

Existing staff accounts become `pending_verification` on upgrade.

## Process

1. **First administrator.** A person with server access checks an organization and its
   administrator, then runs `bootstrap-organization` with the evidence type, a reference and
   their own name. **There is deliberately no web route for this.**
2. **Invitation.** The verified administrator invites by email. The code works once, for
   that email, for 7 days, and is stored only as a hash.
3. **Joining.** The person registers, accepts, and fills in their profile. This grants nothing.
4. **Verification.** The administrator checks evidence outside the software and records the
   profession checked, the evidence type and a reference.
5. **Changes.** Reject, suspend and reinstate all need a reason. Each is written to
   `staff_verifications`, which can't be edited, and to the audit log.

## What the administrator must actually do

**Email or phone proves nothing about qualifications.** A person must check, at least:

- Identity, against a government document.
- Registration, on the relevant council's own register, not a copy supplied by the applicant.
- Employment or engagement with the organization.
- For reviewers, that the qualification matches: pharmacist for medication rules, laboratory
  professional for LOINC mappings.

Which registers apply, and how often to re-check, must be set by the organization. The
software stores no document, only the reference.

## Rules enforced

- Public registration can't set a status, profession or role. `specialist`, `admin` and
  `pharmacist` are refused as account roles. Specialists register as staff and record their
  speciality.
- Nobody can verify, suspend or change the role of their own account.
- Only an administrator of a verified organization can verify, and only its own members.
- Pending, rejected or suspended administrators can do nothing.
- Self-created organizations stay unverified: they can't verify staff, use AMR Guard or
  receive referrals.
- Once verified, profession and registration number are locked.
- Clinician share codes need a verified account, and a care team place stops working if
  verification is lost.
- Reviewer duties use the profession the administrator checked, never one typed into a profile.

## Limits

- The bootstrap operator is a trusted person. Use two people, and keep a paper record.
- A suspension applies to the whole account across organizations.
- Registration expiry and periodic re-verification aren't built.
- `operator_cli` actions on signoffs appear in their own table, not the audit log.
