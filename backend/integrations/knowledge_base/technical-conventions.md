---
id: technical-conventions
title: Technical conventions
category: technical_knowledge
version: 1.0
source: agent_context.md
review_status: technical_reviewed
clinical_content: none
---

# Technical conventions

- The backend is FastAPI with SQLite. The frontend is plain JavaScript modules with no build step.
- Events go through the CareFlow event service. The outbox row commits with the change it describes.
- Event metadata has an allowlist, and events hold no free text.
- Reading tools prefer Gemini, then fall back to Groq. Speech uses Groq.
- Tests use fakes, and never need network access. Live checks run only on request.
