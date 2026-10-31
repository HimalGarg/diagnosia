---
id: handoff-rules
title: Handoffs between tools
category: workflow_instructions
version: 1.0
source: backend/careflow/contract.py
review_status: technical_reviewed
clinical_content: none
---

# Handoffs between tools

No tool produces a handoff in Diagnosia today. The handoff support stays in place for a future tool, so an agent must not assume any handoff exists.

## Rules for the receiving tool

- Check authorization again. A handoff grants nothing.
- Read the source record again. A handoff carries references, not content.
- Human review stays required.

ClinScribe doesn't take part in a handoff. It drafts from the consultation transcript only.
