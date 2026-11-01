# Agent context: Diagnosia

Read this first. It tells you what this project is, how it's built, what you must never
break, and what's still unfinished. Where it disagrees with the code, trust the code and fix
this file.

## What it is

Diagnosia is a healthcare platform for India, built by a student team. It started as a school
project in 2023 and is being rebuilt into something pitchable (Mesa Venture League 2027, and a
hackathon demo). The idea: India has too few doctors, so consultations last about two minutes.
Diagnosia has four AI tools that help doctors work faster and help patients understand their care.

| Tool | Who uses it | What it does | Status |
|---|---|---|---|
| **DiagnoFast** | Doctors; patients answer | Interviews the patient by voice in their own language while they wait, fills the doctor's custom form, adds past records from ABHA, and writes a one-page report | Built and tested live. ABHA is **simulated** |
| **Skin Cancer Detection** | Doctors | Screens a dermoscopic lesion image and gives an early second opinion, with a confidence score and a heatmap | Working **research demo**, not clinically validated, **not licensed for sale** |
| **Prescription Analyzer** | Patients | Reads a photo of a prescription, decodes shorthand, and explains it in plain Hindi or English with audio, diet, yoga and schedule | Built and tested live |
| **OptiKinesis** | People who can't use their hands | Hands-free cursor control with a webcam | **Separate repo** (see the end). Don't work on it here |

Removed tools (Talkie, Curl Counter, BMI Calculator, DocFinder, the old Symptom Checker and
Prescription Generator) and the sidelined VisionTrack live in `archive/`. Nothing there is
served or maintained. VisionTrack may come back, so don't delete it.

More background: `PROJECT_DESCRIPTION.md` (the product, in plain language), `README.md` (setup),
`pitch/` (pitch statistics with sources, video script, slides).

## Repo layout

```
web/        The public site. The ONLY folder served to visitors.
            Webflow-style HTML + css/ js/ images/ icons/. No build step.
backend/    One FastAPI app: accounts and one folder per tool.
  app.py            Wires everything together and serves web/
  auth.py           Sign-up, login, sessions (Argon2, HttpOnly cookie)
  db.py             SQLite schema (users, sessions, intakes)
  migrations.py     Numbered schema changes for existing databases
  safety.py         Pilot readiness checks and signoffs
  private_files.py  Owner-only files and folders, on Linux, macOS and Windows
  settings_store.py Models, tool switches and defaults, set in the app
  operator_cli.py   Command-line setup, keys, backups, first administrator
  llm.py            Groq and Gemini calls, with fallback. ask(system, content, schema, tool)
  speech.py         Groq transcription/WAV speech; Edge neural question playback
  images.py         Upload checks (JPG/PNG, size)
  config.py         Settings from environment / .env
  prescription/     analyzer.py, shorthand.py, medicines.py, routes.py
  diagnofast/       agent.py, abha.py, recordings.py, routes.py, forms/*.json
  records/          The shared patient record, access gate, audit, encrypted documents
  clinscribe/ lablens/ referralloop/ dischargecare/ reminders/   One folder per tool, each with its schema.sql
  skin/             classifier.py, encoder.py, routes.py, training/, metrics.json
  careflow/         Activity timeline: event contract, outbox, delivery, routes
  integrations/     Optional Meko and YugabyteDB adapters, and AI run records
  patient_pack/     One searchable view of a patient's records, on Meko: render, sync, access, brief, routes
  tests/            pytest suite (no API keys needed)
archive/    Retired tools. See archive/README.md.
pitch/      Pitch material.
datasets/, backend/skin/weights/, backend/storage/   Not in git (see below).
```

Pages: `index.html` (the public site, one catalogue of all 8 tools), `care.html` (the workspace,
where every tool runs), `*_hiw.html` info pages, the patient-facing `intake.html` (no account),
`login.html`, `optikinesis_download.html`. `diagnofast.html`, `skin_cancer.html`,
`prescription_analyzer.html` and `dashboard.html` only redirect into the workspace.

The tool list has one source: `web/js/care/catalogue.js`, in the order of a visit. The rail and Home
read it. `index.html` repeats it by hand, and `test_site.py` checks that both agree.

## Run it

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
copy .env.example .env        # add the keys, see below
.\.venv\Scripts\python.exe -m uvicorn backend.app:app --reload
.\.venv\Scripts\python.exe -m pytest backend\tests
```

Open <http://127.0.0.1:8000>. `python -m backend.operator_cli setup-local` creates demo accounts.
Staff accounts are **not** self-declared: a doctor stays pending until an organization administrator
verifies them, and the first administrator is only created from the command line.

Without keys the site still runs and the tools show a friendly error. The tests use fakes, so
they never need keys or network access.

**Keys:** a free `GROQ_API_KEY` and `GEMINI_API_KEY`, each in Settings or in `.env` (git-ignored). One is enough. Ask the project owner
for it. Never put a key in code, a test, a doc, a commit or a chat message.

**Skin model** needs extra setup and large files that aren't in git. See "Set up the skin
model" in `README.md`. Without them `/api/skin/analyze` returns 503 and says the model isn't
installed. The rest of the app is unaffected.

## How the tools work, and why

### Prescription Analyzer (`backend/prescription/`)
Handwriting reading is the risky part, so **nothing is explained until the patient confirms it**.
1. `/api/prescription/read` takes up to 4 photos of one prescription (front and back). Groq's
   vision model
   returns a fixed structure, marking each field `clear`, `unsure` or `unreadable`. It's told never
   to guess.
2. `shorthand.py` decodes OD, BD, TDS, HS, SOS, BBF, `1-0-1`, `x x 1` and so on with plain rules,
   not AI. It returns nothing rather than guessing. A fixed schedule is withheld when the marks
   disagree with the written frequency, and SOS/PRN medicines never get a fixed time.
3. `medicines.py` checks each name against a list of about 250,000 Indian medicines (rapidfuzz).
   Found, close ("Did you mean ...?") or not found. It only flags names. It **never changes** what
   was read. A "clear" name that isn't in the list is downgraded to "unsure".
4. The patient ticks each medicine that matches their prescription. Unticked or unreadable ones go
   to "ask your pharmacist" and **never reach the explanation step**.
5. `/api/prescription/explain` explains only the confirmed medicines, in the patient's language.
   It never changes a dose or suggests a substitute.

The photo isn't stored. A consent box says it goes to outside AI services.

### DiagnoFast (`backend/diagnofast/`)
- Six specialty forms live in `forms/*.json`: general medicine, dermatology, cardiology, orthopedics, ENT and pediatrics (no form builder).
- Reception creates a **single-use link** for each patient. The patient needs no account. The
  link stops working when the interview ends. Only a hash of each link is stored.
- `intake.html` without a token opens the self-service specialty grid when
  `DIAGNOFAST_KIOSK_DOCTOR_ID` names a verified doctor. New visits go only to that doctor;
  public callers cannot attach a chart, choose a doctor or fetch ABHA records. The kiosk is
  unavailable when unconfigured or when that doctor loses verification.
- The interview is a **controlled loop**, not a free-running agent. The AI picks the next question
  and extracts fields. `agent.apply_turn` keeps the rules in code: only fields on the form are
  kept, required fields must be filled, there's a maximum of 12 questions, and urgent symptoms
  (chest pain, trouble breathing and similar) end the interview at once and send the patient to
  reception. The AI never diagnoses.
- Voice: Edge neural voices read questions in Indian English (Neerja) and Hindi (Swara).
  `DIAGNOSIA_NEURAL_TTS=0` disables this service. After an 8-second neural timeout, English
  can use Groq WAV and Hindi uses the browser. Browser voice and typing remain fallbacks.
- The two-column interview shows live browser recognition and optional answer chips. Recordings
  stop at 28 seconds. Where live recognition is unavailable or incomplete, Groq prepares a
  transcript preview. Patients edit and confirm before sending. Confirmed text wins over audio
  recognition; original audio is retained only when encryption is configured. Gemini is preferred
  for the next-turn field normalization, with the normal configurable provider fallback.
- A link can belong to a patient record. Its report then appears in that patient's chart for the
  care team, through the normal access gate and audit log. Recordings are encrypted with the
  document key ring (`recordings.py`). Without a key ring, no recording is kept.
- `abha.py` has one interface with a simulated version. The records stay flagged `is_demo_data`, but the screens no longer say so (see Rules).
  Real ABHA access needs ABDM sandbox approval, an exit review and a security audit.
- Doctors see a queue (urgent patients first) and a printable requisition: local intake barcode,
  demographics, reported ROS, provenance-labeled history, investigation suggestions for physician
  review, and a blank physician workspace. H*/L* require supplied measured values and ranges;
  unasked symptoms are never normal. Original answers and recordings remain expandable. Reports print on one A4 page; if entries do not fit, the printout explicitly says it is incomplete
  and directs the doctor to the full report, original answers and transcript on screen. AI output remains unreviewed.

### Skin Cancer Detection (`backend/skin/`)
- PanDerm (ViT-L) encoder, plus a classifier trained on ISIC 2019 by `training/` scripts (split by
  lesion, so no lesion is in both training and test). Eight classes: MEL, BCC, SCC, AK, BKL, DF,
  NV, VASC.
- Measured on 3,619 unseen images (`metrics.json`): macro AUROC 0.934, melanoma AUROC 0.881,
  balanced accuracy 60.5%, 90.1% of melanomas and 94.3% of concerning lesions sent for referral,
  and 42.7% of benign lesions also referred. It's a cautious screener, not a precise classifier.
  Say so honestly. Never call it a diagnosis.
- Images unlike the training data (blurry, wrong picture, not skin) come back **"uncertain"** with
  no scores. This uses Mahalanobis distance. The earlier cosine-similarity check caught only 8% of
  non-skin images, and this one caught all 95 tried.
- The heatmap comes from **occlusion** (hide each patch and measure the drop). Grad-CAM and
  attention maps were tried first and showed almost no agreement with it, so they would have
  misled doctors. Don't swap them back in without re-testing.
- Needs a GPU: about 3 seconds per image on a 6 GB RTX 4050, but about 2 minutes on a CPU.
- Hidden behind `get_classifier()` and the `SkinClassifier` protocol, so the model can be
  replaced without touching the rest of the app.

## Rules that must not be broken

**Patient safety**
- The AI never diagnoses, never changes a dose, never suggests a substitute medicine, and never
  tells a patient something is or isn't serious.
- Unreadable or unconfirmed medicines are never explained.
- Doctors stay in charge. Every clinical tool assists and never decides.
- The skin result always says it is not a diagnosis. The owner removed the "research demo", "DEMO DATA" and pilot labels from the screens for the demo. The skin tool is still an unvalidated research model and ABHA is still simulated, so bring them back before any real use, and say so when presenting.

**Privacy**
- Use **made-up patients** only. No real patient data until privacy and security are reviewed.
- Never commit prescription photos, audio or databases. `.gitignore` covers `Prescriptions*.jpg`,
  `backend/storage/` and `*.db`. Check before you `git add`.
- Groq receives this data in the United States, with no signed agreement yet. Keep personal details
  out of test photos. See `docs/CLINICAL_GOVERNANCE.md`.
- Meko has no signed agreement either, and states nothing about compliance. Patient packs stop when
  `ALLOW_REAL_PATIENT_DATA` is on. Don't remove that.

**Licences**
- **PanDerm** (code and weights) is CC-BY-NC-ND 4.0, non-commercial academic research only.
  **ISIC 2019** is CC-BY-NC. So the skin tool must stay out of paid plans and be replaced before
  anyone is charged. The likely replacement is DINOv2 (Apache 2.0) trained on the ISIC images
  licensed CC-0 or CC-BY.
- **Never commit** PanDerm code or weights, ISIC images, or the medicine dataset. They live in
  git-ignored `datasets/` and `backend/skin/weights/`.
- The medicine dataset repo is MIT, but the data looks scraped from pharmacy sites, so its
  upstream permission is unconfirmed. Fine for a demo. Replace it before selling.

**Security**
- All SQL uses parameters. Passwords are Argon2. Session tokens are stored hashed.
- Page text from patients and from the AI goes in with `textContent`, never `innerHTML`.
- Only `web/` is served. Don't add routes that serve other folders.
- Never log keys, patient text or full AI answers.

## Conventions

- Python 3.11. Match the style of the surrounding code, which is plain, with short comments that
  say **why**.
- New AI behavior goes behind a small function with a Pydantic schema, called through `llm.ask`.
  New speech behavior goes through `speech.py`.
- **Tests must not need keys or network.** Fake `llm.ask` and `speech` as the existing tests do. Put
  new tests in `backend/tests/` and run the whole suite before opening a pull request.
- The frontend is plain HTML, CSS and JavaScript. No framework and no build step. A new tool is a
  module in `web/js/care/`, one entry in `catalogue.js`, and the renderer in `app.js`. Its text goes
  through `h()`, and its colours come from the tokens in `care.css`. Only `intake.html` still uses
  `tools.css`. The public pages use the Webflow template in `index.css`.
- Only the microphone browser check is in the repo (`backend/tests/browser/`). After a UI change,
  test it in a real browser at desktop, tablet and phone widths.
- Don't add dependencies without a reason. `backend/requirements.txt` is the app,
  `backend/requirements-skin.txt` is the heavy skin tool.
- A new tool's routes join the sweep in `test_security.py`. Add a real gate, not a list entry.
- Code and tests must work on both Windows and Linux. Protect sensitive files with
  `private_files.py`, because `chmod` does nothing on Windows. Pass `encoding="utf-8"` when reading
  text, because Windows otherwise uses cp1252.

## CareFlow

Events go through `backend/careflow/events.py`, never straight to a store. The outbox write shares the
transaction with the change, usually through the audit hook. Events hold no free text, so don't add
any: a new metadata key needs a reason. SQLite stays the record, and nothing flows back from MongoDB,
YugabyteDB or Meko. See `docs/integrations/`.

## Patient packs

One Meko datapack per patient, used as a search index. See `docs/integrations/PATIENT_PACKS.md`.

- A finished record is queued with `sync.enqueue`, in the same transaction. Only finished records go in:
  never a draft, a pending result, a transcript or an unconfirmed medicine.
- SQLite decides access, in `patient_pack/access.py`. Anything shown or given to the AI is read again
  from SQLite. Meko's text never reaches a prompt or a screen.
- Off by default, and stopped by `ALLOW_REAL_PATIENT_DATA`. There's no agreement with Meko, so made-up
  patients only.
- In demo mode every consent is recorded by default, marked `demo_default`, and the workspace has no
  consent panel. `care.consent_by_default` must stay false with real patient data. Before real use, the
  consent panel, the record of who opened a chart, FHIR export and the pilot warnings need bringing back.
- Clinical text never goes in Meko memory or conversations.
- Meko never returns a datapack slot, so live tests must not create them freely.

## Team workflow

- `main` is the shared, working version. **Don't commit to `main` directly.**
- Branch from `main` for each piece of work, one tool or feature per branch, named like
  `feature/skin-dinov2` or `fix/intake-timeout`. Open a pull request into `main`.
- Run `pytest backend/tests` before opening it, and say what you tested by hand.
- Pull `main` into your branch often. Likely collision points are `backend/app.py` (router
  registration), `backend/requirements.txt`, `web/css/index.css` and `backend/db.py`.
- **Schema changes are migrations.** New tables go in a tool's `schema.sql`. A change to an existing
  table is a numbered step in `backend/migrations.py`, which runs once at start-up. Never ask anyone
  to delete a database.

## Known gaps and open work

- **Not hosted.** Netlify can't run this backend. It needs a Python host, and the skin tool needs a
  GPU. Set `DIAGNOSIA_HTTPS=1` in production.
- **No rate limiting.** Anyone with an account can run up AI costs, and patient links can be
  guessed at only by luck (they're long random tokens). Add limits before a public launch.
- **Prescription reading varies between runs.** The same photo can read `Azu` one time and `Azee`
  the next, and a confident "clear" can still be wrong. The patient confirmation step is the safety
  net. An idea not built: read each prescription twice and flag fields where the readings differ.
- **Live on both, lightly.** A Hindi interview and a synthetic prescription work on Groq and Gemini.
  Reading quality on real handwriting and real Hindi speech is untested. Run `backend/tests/live/`
  with `DIAGNOSIA_LIVE_TESTS=1` after changing a model.
- **Reading tools use Gemini first.** Prescription reading and explaining, and lab report reading, are pinned to Gemini (own settings), after a comparison on synthetic photos in `backend/tests/ai_eval/`. Re-run it when models change.
- **Model choice.** Each tool's Groq model is a setting, and Gemini has one model for the other tools. A
  failed call goes to the other provider, or providers take turns (`ai.strategy`). With one provider,
  a failed call is retried once. A DiagnoFast question times out at 45 seconds per provider.
- **Languages.** Hindi and English only.
- **ABHA** is simulated. **Regulation:** CDSCO's medical device software guidance (July 2026)
  may put the skin tool in a class needing a licence. Get a regulatory review before any real use.
- **Cost** (rough, from measured tokens): about Rs 2 per prescription and Rs 2 to 2.50 per
  DiagnoFast intake, measured on the earlier Gemini setup. Re-measure on Groq before quoting prices.
- **Site text.** `terms_of_service.html` hasn't been reviewed for the new tools. The team section
  is owned by the project owner. Leave it.
- The earlier symptom-checker, prescription and BMI features are gone. Don't rebuild them.

## Things that look wrong but aren't

- The OptiKinesis **download button leads to a 404**. It points at a GitHub release that doesn't
  exist yet. This is deliberate for now, because the owner demos OptiKinesis themselves.
- Skin results are "uncertain" for slides, photos of people and similar images. That's the
  familiarity check working.
- Many tests patch `llm.ask`, `speech.speak` and the medicine list on purpose.

## Related

- **OptiKinesis** (webcam head-pose cursor, blink to click, on-screen keyboard) is a separate
  project: <https://github.com/HimalGarg/OptiKinesis>. It has its own `AGENTS.md`. Don't change it
  from this repo.
- Pitch: Mesa Venture League 2027 qualifier (video and optional deck). Material is in `pitch/`.
  Every statistic there has a source, and `pitch/MVL27_PITCH_STATS.md` lists numbers to avoid.
