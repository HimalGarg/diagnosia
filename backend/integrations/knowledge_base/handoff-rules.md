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

Only two handoffs exist in Diagnosia. An agent must not assume any other.

## LabLens to ChronicCare

A person confirms a lab report in LabLens. Its results become observations in the record, and ChronicCare's trends read those observations. ChronicCare must read the observations from the record. Nothing about their values is carried in the handoff.

## Prescription Analyzer to MedGuard

The patient confirms each medicine, and the confirmed list is imported into MedGuard's medication list. Unconfirmed or unreadable items stay out. MedGuard must read the list from the record.

## Rules for the receiving tool

- Check authorization again. A handoff grants nothing.
- Read the source record again. A handoff carries references, not content.
- Human review stays required.

ClinScribe doesn't take part in a handoff. It drafts from the consultation transcript only.
