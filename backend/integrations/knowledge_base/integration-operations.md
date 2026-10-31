---
id: integration-operations
title: Integration operations
category: integration_operations
version: 1.0
source: docs/integrations/TROUBLESHOOTING.md
review_status: technical_reviewed
clinical_content: none
---

# Integration operations

- MongoDB Atlas holds the activity timeline. YugabyteDB holds technical AI run and handoff records. Meko holds shared context.
- If a destination is down, events wait in the outbox and retry with growing delays. After too many failures an event is marked dead and can be replayed.
- A repeated delivery can't create a duplicate.
- A failed search for context is reported as unavailable. It's never treated as finding nothing.
- Check status with the integrations-status command.
