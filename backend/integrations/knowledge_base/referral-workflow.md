---
id: referral-workflow
title: Referral workflow steps
category: referral_workflow
version: 1.0
source: backend/referralloop/routes.py
review_status: technical_reviewed
clinical_content: none
---

# Referral workflow steps

ReferralLoop's steps, as the tool records them. This describes workflow, not clinical decisions.

1. A clinician creates a referral to a receiving organization, with a reply-by date.
2. The referral is sent. The receiving side acknowledges it.
3. The receiver may ask for more information, accept, decline or redirect.
4. An appointment is offered, booked and sometimes rescheduled. Contact attempts are recorded.
5. The patient attends, or a no-show is recorded.
6. The outcome is returned. The referrer reviews it, and the referral is closed.

A referral that passes its reply-by date is surfaced to staff. The receiver sees the referral and the chosen files, never the whole record.
