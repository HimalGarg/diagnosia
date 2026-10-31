---
id: workflow-rules
title: Workflow rules for every tool
category: workflow_instructions
version: 1.0
source: docs/CLINICAL_GOVERNANCE.md
review_status: technical_reviewed
clinical_content: none
---

# Workflow rules for every tool

These rules apply to any agent or tool working in a Diagnosia workflow.

1. A person reviews all AI output. Generating something never counts as approving it.
2. The Diagnosia database is the record. Context from any other system is supplemental.
3. Consent is checked by the tool that needs it, each time. Context is never proof of consent.
4. Identity is checked against the source record. Context is never proof of identity.
5. Use pseudonymous workflow references. Don't copy names, contact details or record contents into shared context.
6. When something is unreadable or unsure, say so and hand it to a person. Don't guess.
7. A failed step is reported as failed. Nothing is recorded as done until it is done.
