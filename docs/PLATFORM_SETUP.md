# Platform setup

How to get Diagnosia working end to end on one machine, and what can be configured.

## First run

```bash
uv pip install -r backend/requirements.txt
.venv/bin/python -m backend.operator_cli setup-local
.venv/bin/uvicorn backend.app:app --host 127.0.0.1 --port 8001 --reload
```

`setup-local` runs in development only, and only once. It:

- creates the document encryption key at `~/.diagnosia/document-keys.json`, outside the project
- adds the settings it needs to `.env`
- creates a verified demo clinic, a demo specialist centre, and demo accounts
- runs a synthetic patient journey through the record-based tools and leaves a DiagnoFast link waiting, using the real API
- saves the logins to `~/.diagnosia/local-accounts.txt`

**Back up the key file.** Losing it makes stored documents unreadable.

It approves no clinical content and records no governance signoff. MedGuard rules and LOINC
mappings stay unreviewed, so you can try the review workflow with the demo pharmacist and
laboratory accounts.

## Turning on AI

Create a free key at [console.groq.com](https://console.groq.com). Sign in as the demo doctor,
open **Settings**, and paste it under AI key. The key is stored encrypted and never shown again.
You can use `GROQ_API_KEY` in `.env` instead, which takes priority.

### A second provider

Add a free Gemini key from [aistudio.google.com/apikey](https://aistudio.google.com/apikey) in the same
place, or as `GEMINI_API_KEY`. With both keys:

| Setting | Meaning |
|---|---|
| Provider tried first | Groq or Gemini. Default Groq |
| When both providers have a key | **fallback**: the other one answers only when the first fails. **round_robin**: they take turns, and a failure still falls back to the other |
| Gemini model | One model for every tool. Default `gemini-3.5-flash-lite` |

Gemini carries text and images. Speech to text and the English voice stay on Groq, and the browser's
own voice covers the rest, so Gemini alone can't transcribe a consultation. A failed call is logged
by provider name only, never with its content. **Both providers receive patient content**, so each
needs its own agreement before real use.

Without a key, everything except drafting, speech and reading scans still works, and each screen
says what is missing.

## What the demo accounts do

| Account | Try this |
|---|---|
| Doctor and clinic administrator | Every tool, Settings, staff verification |
| Specialist | Received referrals: accept, book, return an outcome |
| Pharmacist, second pharmacist | Add evidence to a rule, submit, approve as the other person |
| Laboratory reviewer | Approve or correct LOINC mappings |
| Patient | Their own record, readings, checklist, on a phone-sized screen too |

## What Settings controls

| Area | What you can change |
|---|---|
| AI key | Add, replace or remove |
| AI models | The model for each job. Any model name the provider supports is accepted |
| Tools | Switch any tool on or off, including DiagnoFast, Prescription Analyzer and Skin Cancer Detection. Off applies to the API too, not only the screen |
| ClinScribe | Default language, your clinic's own note headings |
| LabLens | Whether AI reads reports we can't read locally |
| ReferralLoop | Suggested reply times |
| Reminders | Hours a patient has to confirm |
| Safety wording | The emergency instruction patients see |

Only an administrator of a verified organization can change these, and each change is audited.
Safety switches, such as allowing real patient data, are deliberately not settings.

## Models

Chosen from Groq's documentation, checked 31 October 2026. Availability changes, so check
[Groq's model list](https://console.groq.com/docs/models).

| Job | Default | Why |
|---|---|---|
| Clinical notes | `openai/gpt-oss-120b` | Production model with guaranteed schema output |
| Prescription explanations | `openai/gpt-oss-20b` | Faster, same guarantee |
| Prescription and lab images | `qwen/qwen3.8-27b` | The vision model open to all accounts. **Groq lists it as preview** |
| Speech to text | `whisper-large-v3` | Groq's choice for error-sensitive multilingual use |
| Reading aloud, English | `canopylabs/orpheus-v1-english` | Preview. May need its terms accepted in Groq's console |
| Reading aloud, Hindi | Browser voice | Groq has no Hindi voice |

Groq's documents don't name Hindi for Whisper, so test Hindi transcription with your own key.

## Free plan limits

Groq's page didn't make clear whether the figures it showed were the free plan's, so check
[your limits](https://console.groq.com/settings/limits). They appeared to be about 30 requests a
minute and 1,000 a day for the chat models, with 8,000 tokens a minute. A long consultation could
exceed that, and the app then reports the limit rather than failing silently. Speech appeared to be
20 requests a minute. These limits suit a demo and a small pilot, not a busy clinic.

## Other free providers

Any OpenAI-compatible service can replace Groq by setting `AI_BASE_URL`, such as a local model
server. Choose models in Settings. This route hasn't been tested with a real alternative.

## Moving beyond a local demo

`setup-local` is for synthetic data on your own machine. For anything else, follow
`PRODUCTION_DEPLOYMENT_CHECKLIST.md`.
