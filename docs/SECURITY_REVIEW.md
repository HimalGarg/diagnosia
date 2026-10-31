# Security review

An internal code review with automated and browser tests, finished 31 October 2026.
**It is not an independent penetration test,** and one is required before real use.

## Automated coverage

`backend/tests/test_security.py` sweeps every API route, so new routes are included
automatically.

| Area | What is enforced | Evidence |
|---|---|---|
| Authentication on all routes | Every non-public route returns 401 without a session. DiagnoFast's three patient routes use the link instead, and a made-up link opens nothing | Route sweep |
| Patient-to-patient isolation | Another patient gets 403 or 404 on every patient read | Route sweep |
| Doctor-to-patient | Access needs a care team place and a verified account | Route sweep, staff tests |
| Writes guarded | Each route's code is read to confirm it calls an access gate. Empty-body requests alone can't prove this, because validation runs first | `test_every_route_touching_a_record_goes_through_a_gate` |
| Referral scope | A receiver sees the referral and chosen files, not the record | Referral tests |
| Cross-organization | Roles and staff decisions stop at the organization | Staff, AMR tests |
| Consent revocation | Takes effect on the next request, for referral, document, inbox and new sends | `test_the_consent_revocation_reach` |
| Sessions | Hashed tokens, server-side logout, expiry, suspended accounts lose sessions, 12-hour staff sessions | Auth, security tests |
| Cookies | HttpOnly, SameSite=Lax, Secure with `DIAGNOSIA_HTTPS=1` | Security tests |
| CSRF | Origin and Sec-Fetch-Site checked on writes, plus SameSite | Security tests |
| CareFlow access | Each read is limited to workflows the person can open in SQLite, so a store can't widen access. Revocation applies at once | CareFlow tests |
| Sign-in redirect | Only paths inside the workspace are accepted, so a crafted link can't send someone elsewhere | Site test |
| XSS | All page content is text nodes, no `innerHTML`, strict script policy on the workspace | Site tests. In Chrome, a hostile patient name rendered as text and did not run |
| SQL injection | Parameterised queries, static scan, hostile values stored as text | Security tests |
| IDOR | Hostile and foreign IDs find nothing | Security tests |
| Uploads | Type from bytes, 20 MB limit, size checked first, kept in memory | Document tests |
| Downloads | Authorised per request, nosniff, safe disposition | Document tests |
| Path traversal | Random stored names, filename stripped | Site, document tests |
| Logs | Exception type and request ID only | External failure tests |
| Caching | `no-store` on all API answers | Security tests |
| Audit | Actor, action, target, time, request ID. Triggers block edits and deletes. No patient content. Patients can see who accessed their record | Security tests |
| Brute force | 10 failed logins per email in 15 minutes | Staff tests |

## Findings fixed in this review

| Finding | Severity | Fix |
|---|---|---|
| Anyone could become an active doctor | S0 | Account states and verification |
| Any doctor could self-create an administrator organization | S0 | Unverified organizations can't verify staff or use AMR Guard, or receive referrals |
| Exception text in logs could hold patient details | S1 | Type and request ID only |
| No `no-store`, so data could stay in browser cache | S1 | Cache headers |
| Uploads over 1 MB spooled to plaintext temp files | S1 | Held in memory, capped |
| No login throttle, 14-day staff sessions | S1 | Throttle, 12 hours |
| Revoked `maternal_care` changed nothing | S1 | Enforced on clinical actions |
| Last audio chunk lost on pause | S1 | Waits for the upload |
| Stop during the permission step left the microphone on | S1 | Stop now works at any moment |
| A left session stayed marked recording | S2 | Paused on leaving |
| No security headers | S2 | Added |
| DiagnoFast recordings were written unencrypted, outside backups and key rotation | S1 | Encrypted in the documents folder, and covered by backup, rewrap, retire and verify |
| The prescription route needed a larger body limit | S2 | 45 MB on that route only. Every other route stays at 25 MB |

## Open findings

| Finding | Severity | Owner |
|---|---|---|
| No multi-factor authentication for staff | S1 | Technical work |
| No password change or reset. The dashboard button does nothing | S1 | Technical work |
| Database is plaintext. Protection relies on the disk | S1 | Infrastructure |
| Audit triggers stop the application, not someone with file access. Logs need shipping off the host | S1 | Infrastructure |
| A suspended account is suspended everywhere, by any organization it belongs to | S2 | Product decision |
| Throttling is per email, not per address | S2 | Technical work |
| Sessions have no idle timeout | S2 | Technical work |
| An open CareFlow stream keeps its workflow list until it reconnects. It only ever says that something changed, and the reload rechecks access | S3 | Accepted |
| CareFlow's references are pseudonymous, but event timing could still identify someone to a person who knows the visit | S2 | Keep to synthetic data, and review before any real use |
| DiagnoFast links are bearer tokens in a URL, so they appear in server access logs until used | S2 | Set log retention. Links are single use |
| No rate limit on DiagnoFast links or AI routes, so a signed-in user can run up AI costs | S2 | Technical work |
| DiagnoFast has no retention limit for recordings | S2 | Policy decision |
| Server access logs hold URLs, with record IDs and drug-name lookups | S2 | Set log retention |
| Filenames in the database can hold names | S2 | Covered by disk encryption |
| Uploads aren't virus-scanned, and PDFs are parsed by third-party code | S2 | Infrastructure |
| The public marketing pages include analytics. The workspace doesn't | S2 | Product decision |

## Not tested

Load, denial of service, dependency vulnerability scanning, infrastructure, TLS, and real
attack attempts by an outside tester. Firefox and Safari weren't tested.
