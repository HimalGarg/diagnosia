# Diagnosia — Add-on Tools Blueprint

> **Update, 1 November 2026:** MedGuard, ChronicCare, MaternaCare and AMR Guard were removed from Diagnosia after this was written. It is a record of 31 October 2026 and still describes them. The current tools are in the README.

**Document type:** Product, technical architecture, and open-source evaluation brief  
**Version:** 1.0  
**Prepared:** 31 October 2026  
**Status:** Planning document — not a clinical validation or regulatory approval document

---

## 1. Purpose of this document

This document defines eight proposed add-on tools that can expand Diagnosia from a collection of individual healthcare utilities into a connected patient-care platform. For every add-on, it describes:

- The problem it solves and the users it serves.
- The expected end-to-end workflow.
- Functional modules and important data requirements.
- How it should connect with Diagnosia's existing services.
- Open-source projects, codebases, and standards that may be reused or studied.
- Technical limitations, licensing considerations, and clinical-safety boundaries.
- A practical implementation direction.

The open-source projects below are **starting points for a technical audit**, not endorsements and not claims that the projects are ready for clinical deployment. A public repository may be a prototype, a sample application, a workflow reference, or a library rather than a complete product. Before copying code, inspect the current default branch, commit history, dependencies, tests, security posture, licence, and third-party model/data terms.

## 2. Diagnosia context and design assumptions

The current Diagnosia architecture described in the project documentation and prior planning discussions includes:

- **Backend:** FastAPI, SQLite, SQLAlchemy, and Pydantic.
- **AI services:** Claude integration and Sarvam AI services.
- **Authentication:** Argon2id password hashing, hashed session tokens, and HttpOnly cookies.
- **Frontend:** HTML, CSS, and JavaScript served alongside the API.
- **Existing tools:** DiagnoFast voice-first patient intake, Skin Cancer Detection, and Prescription Analyzer.
- **Related but separate product:** OptiKinesis, an accessibility/control product. It should remain a separate product and code boundary; Diagnosia may offer compatible accessibility pathways later, but it should not be treated as a clinical decision-support module.

The architecture may have evolved since the documentation was written. Claude Code or the implementation team should inspect the actual repository before changing schemas, route names, or service boundaries.

### 2.1 How the eight add-ons fit together

| Add-on | Primary user | Main job | Relationship to existing Diagnosia tools |
|---|---|---|---|
| **ClinScribe** | Doctor / clinician | Turn a clinician-patient encounter into a reviewable clinical note | Complements DiagnoFast: patient-side intake versus clinician-side documentation |
| **MedGuard** | Doctor / pharmacist | Reconcile medication lists and surface medication-safety issues | Extends Prescription Analyzer from reading a prescription to maintaining a medication history |
| **ChronicCare** | Patient / care team | Track long-term conditions, symptoms, vitals, goals, and follow-ups | Consumes approved observations and reports from other Diagnosia tools |
| **LabLens** | Patient / doctor / lab staff | Extract, structure, trend, and explain laboratory reports | Adds structured lab data to the patient record; can feed ChronicCare |
| **DischargeCare** | Hospital / doctor / patient / caregiver | Coordinate tasks and follow-up after discharge | Uses MedGuard, LabLens, ChronicCare, and ReferralLoop during transitions of care |
| **MaternaCare** | Pregnant patient / obstetric team / community health worker | Coordinate antenatal, delivery, and postnatal care | A specialised longitudinal pathway using records, reminders, observations, and referrals |
| **AMR Guard** | Hospital / microbiology / stewardship team | Support antimicrobial stewardship and local resistance surveillance | A specialist institutional module; should not be a consumer-facing antibiotic recommender |
| **ReferralLoop** | Referring doctor / specialist / coordinator | Track a referral from request through appointment to returned specialist report | Connects DiagnoFast, ClinScribe, LabLens, and DischargeCare workflows |

### 2.2 Product principle: shared platform, separate clinical workflows

Do not build eight disconnected mini-applications. Each add-on should be a module inside the Diagnosia platform and use shared identity, patient records, encounters, permissions, notifications, audit history, and document storage.

At the same time, do not force all clinical workflows into one generic AI agent. Each module must have its own domain logic, validation rules, permissions, and review states. AI can help with extraction, summarisation, and drafting; deterministic code and approved clinical rules should handle validation and workflow state changes.

---

## 3. Shared foundation required before broad integration

The add-ons will be difficult to integrate safely if Diagnosia only stores user accounts and sessions. Build or verify the following shared foundation before connecting all modules.

### 3.1 Core records

| Record | Purpose | Suggested fields / relationships |
|---|---|---|
| `Patient` | The person receiving care | Internal ID, demographics, identifiers, contact preferences, status |
| `PatientIdentifier` | External identifiers | Identifier type, value, issuer, verification state; store only what is necessary |
| `Practitioner` | Clinician or care professional | User link, qualifications, speciality, registration details where applicable |
| `Organization` | Clinic, hospital, laboratory, pharmacy | Name, type, address, contact, external identifier |
| `CareTeam` | People involved in care | Patient, practitioner, role, effective dates, access scope |
| `Encounter` | A particular care interaction | Patient, participants, organisation, type, start/end, status, reason |
| `ClinicalDocument` | Uploaded or generated clinical files | File reference, document type, source, owner, encounter, checksum, retention status |
| `Observation` | A measured or observed result | Code, value, unit, effective time, performer, method, reference range, source |
| `MedicationRecord` | Medication history and current medicines | Drug concept, strength, dose, route, frequency, start/end, status, source, verification state |
| `ConsentRecord` | Permission for a defined purpose | Subject, purpose, scope, granted/revoked time, method, policy version |
| `AuditEvent` | Trace of access and changes | Actor, action, patient/resource, timestamp, reason, outcome, request ID |
| `Task` | Work that someone must complete | Owner, due date, priority, status, linked patient/encounter/referral/discharge |
| `Notification` | Reminder or workflow notification | Recipient, channel, template, event, delivery status, opt-out status |

Use internal UUIDs as primary keys. External health identifiers should be identifiers, not database primary keys. Do not assume that a patient, user account, and practitioner are the same entity.

### 3.2 Clinical provenance and review states

Every clinical datum should have a source and review state. Examples:

- `source_type`: patient-entered, clinician-entered, imported document, laboratory result, device, or AI-extracted.
- `source_reference`: original document, encounter, laboratory, or external resource.
- `extraction_method`: OCR engine, parser version, model/provider, prompt version, or manual entry.
- `review_status`: pending, accepted, corrected, rejected, or superseded.
- `reviewed_by` and `reviewed_at` where a human review occurred.

Never silently convert an AI guess into a confirmed patient fact. Preserve the original input and the corrected value when a reviewer changes an extraction.

### 3.3 FHIR and India interoperability direction

Use HL7 FHIR R4 as the interoperability representation where appropriate, without requiring every internal table to be a literal FHIR resource. Important mappings include:

- `Patient`, `Practitioner`, `Organization`, `CareTeam`.
- `Encounter`, `Condition`, `Observation`, `DiagnosticReport`.
- `Medication`, `MedicationRequest`, `MedicationStatement`, `MedicationAdministration`, `AllergyIntolerance`.
- `ServiceRequest`, `Appointment`, `Task`, `CarePlan`, `DocumentReference`, `Composition`.
- `Provenance`, `AuditEvent`, `Consent`, `Communication`.

For India-facing interoperability, evaluate the National Resource Centre for EHR Standards / ABDM FHIR Implementation Guide and its profiles and value sets. The version pages surfaced during research include a published v6.5.0 release and a v7.0.0 preview build; the team must confirm the applicable published version and profile requirements at implementation time. Do not label an integration ABDM-compliant merely because it emits generic FHIR R4 JSON.

Useful reference: [ABDM FHIR Implementation Guide downloads](https://www.nrces.in/ndhm/fhir/r4/downloads.html) and [ABDM FHIR v7 preview](https://www.nrces.in/preview/ndhm/fhir/r4/index.html).

### 3.4 Common API and service conventions

Use the existing FastAPI application and current authentication/session mechanisms. Names below are illustrative; final paths must match the inspected codebase.

```text
/api/patients/{patient_id}/encounters
/api/patients/{patient_id}/timeline
/api/patients/{patient_id}/documents
/api/patients/{patient_id}/observations
/api/patients/{patient_id}/medications
/api/addons/{module}/...
/api/audit-events
```

Recommended internal structure:

```text
app/
  api/
    addons/
      clinscribe.py
      medguard.py
      chroniccare.py
      lablens.py
      dischargecare.py
      maternacare.py
      amr_guard.py
      referral_loop.py
  models/
    patient.py
    encounter.py
    clinical_document.py
    observation.py
    medication.py
    task.py
    consent.py
    audit_event.py
  schemas/
  services/
    fhir/
    document_storage/
    notifications/
    permissions/
    clinical_provenance/
  modules/
    clinscribe/
    medguard/
    chroniccare/
    lablens/
    dischargecare/
    maternacare/
    amr_guard/
    referral_loop/
```

Avoid importing an entire external application's authentication, patient database, or frontend unless a deliberate decision has been made to run it as a separately deployed service. Prefer extracting a bounded capability, adapting its data model, or using its workflow as a reference.

---

# 4. Add-on 1 — ClinScribe: AI Clinical Documentation

## 4.1 Product overview

**ClinScribe** is a clinician-side documentation assistant. It helps a doctor capture a consultation, create a structured draft note, and attach the reviewed note to the patient's encounter.

This is different from **DiagnoFast**. DiagnoFast gathers the patient's story before or at the start of a visit. ClinScribe records the actual clinician-patient conversation and turns it into documentation. The two tools can share the same encounter but should have distinct screens and consent steps.

## 4.2 Main users and use cases

- Doctors who spend too much time typing consultation notes.
- Clinics that need consistent documentation across practitioners.
- Specialists who want structured histories, examination findings, assessment, and plan sections.
- Clinical administrators who need notes to be complete before an encounter is closed.

## 4.3 Proposed workflow

1. A clinician opens an existing patient and creates or selects an encounter.
2. The UI explains what is being captured and obtains the required patient/clinician consent.
3. The clinician starts, pauses, resumes, or stops audio capture.
4. Audio is transcribed. The transcript is displayed with timestamps and speaker labels where supported.
5. The note generator prepares a draft in a configurable structure, such as SOAP: Subjective, Objective, Assessment, and Plan.
6. The system highlights uncertain phrases, missing information, possible transcription errors, and unsupported statements.
7. The clinician edits and approves the note. Only the clinician can sign or finalise it.
8. The signed note is stored with its source transcript metadata and linked to the encounter.
9. Audio and transcript are retained or deleted according to the configured consent and retention policy.

## 4.4 Functional modules

- **Encounter capture:** start/stop/pause recording, audio quality status, device selection.
- **Transcription:** speech-to-text, timestamps, optional speaker diarisation, language selection.
- **Clinical note drafting:** SOAP or specialty-specific templates, structured fields, concise summaries.
- **Review workspace:** transcript-to-note traceability, uncertainty markers, inline editing, accept/reject controls.
- **Template management:** clinic-specific headings and documentation conventions.
- **Finalisation:** clinician approval, signature metadata, version history, export to PDF or FHIR `Composition` / `DocumentReference` as appropriate.
- **Audit and retention:** who recorded, accessed, edited, signed, exported, or deleted the note.

## 4.5 Open-source options

### Primary candidate: [OpenScribe](https://github.com/sammargolis/OpenScribe)

- Repository describes an open-source AI medical scribe with encounter recording, transcription, and structured draft-note generation.
- The repository declares the **MIT License**.
- Its default web path uses local Whisper transcription and a hosted Claude note-generation path. It also documents a fully local desktop path using Whisper and Ollama.
- It explicitly states that it is **not HIPAA compliant yet**. This is a critical warning, not a minor implementation detail.
- Its Next.js/Electron structure is not a direct fit for Diagnosia's current FastAPI + HTML/CSS/JavaScript application.

**Recommended reuse:** study or selectively adapt the encounter capture pipeline, draft-note structure, note validation approach, and provider abstraction. Do not copy its storage/authentication model without a separate security review. Do not assume its privacy posture or compliance status transfers to Diagnosia.

### Existing Diagnosia components

DiagnoFast already uses voice capture/transcription and Claude extraction according to the current project description. Reuse those service abstractions where technically appropriate rather than introducing an entirely separate speech stack. ClinScribe still needs its own clinician workflow, note schema, consent controls, and clinical review lifecycle.

## 4.6 Suggested data and API shape

- `ScribeSession`: encounter, clinician, capture state, consent reference, start/end times.
- `TranscriptSegment`: session, speaker, timestamp, text, confidence, language.
- `ClinicalNoteDraft`: encounter, template, generated content, model/prompt version, status.
- `ClinicalNoteVersion`: note, version number, author, changes, signed state.

Illustrative endpoints:

```text
POST /api/addons/clinscribe/encounters/{encounter_id}/sessions
POST /api/addons/clinscribe/sessions/{session_id}/transcribe
POST /api/addons/clinscribe/sessions/{session_id}/draft-note
PATCH /api/addons/clinscribe/notes/{note_id}
POST /api/addons/clinscribe/notes/{note_id}/approve
```

## 4.7 Safety and quality requirements

- Recording must never start silently.
- Clearly show recording state and provide a stop control.
- Do not invent examination findings, diagnoses, doses, or plans that were not said or entered.
- Separate quoted patient statements from clinician observations and AI-generated summaries.
- Mark uncertain names, doses, dates, negations, and medical terms for review.
- A generated note remains a draft until a clinician explicitly approves it.
- Test transcription errors involving negation, laterality, decimal points, drug names, and units.
- Establish audio retention, deletion, access, and breach-response policies before real patient use.

**Implementation priority:** High. A natural first expansion because it complements DiagnoFast and can be piloted in a constrained clinician workflow.

---

# 5. Add-on 2 — MedGuard: Medication Reconciliation and Safety

## 5.1 Product overview

**MedGuard** maintains a reconciled medication list for a patient and helps a clinician or pharmacist identify discrepancies, duplicate therapies, allergy conflicts, and possible drug interactions.

It must not be described as a guaranteed medication-safety engine. Drug interaction checking is only as complete and current as the underlying drug terminology, knowledge base, local formulary, and rules.

## 5.2 Main users and use cases

- Doctors reviewing a patient's existing medicines before prescribing.
- Pharmacists reconciling medicines from different prescriptions or care settings.
- Clinicians reviewing medications during hospital admission, discharge, or specialist referral.
- Patients confirming the medicines they actually take.

## 5.3 Proposed workflow

1. Collect medication records from Diagnosia prescriptions, patient-reported medicines, discharge summaries, imported records, and clinician entry.
2. Normalise each medicine to a drug concept, strength, dose form, route, and schedule where possible.
3. Preserve each source separately; do not overwrite the patient-reported list with a prescription extraction.
4. Compare sources and identify additions, omissions, duplicates, conflicting strengths, or different schedules.
5. Check allergies and possible interactions using a versioned, validated knowledge source.
6. Present findings with severity, evidence/source, date, and an explanation of uncertainty.
7. A clinician or pharmacist confirms the reconciled list and records the decision.
8. Generate a medication reconciliation summary for the encounter, referral, or discharge.

## 5.4 Functional modules

- **Medication list aggregation:** prescribed, patient-reported, administered, discontinued, and historical medicines.
- **Drug normalisation:** ingredient, brand, strength, dosage form, route, and coding.
- **Discrepancy engine:** conflicting dose/frequency, duplicates, missing medicines, stale entries.
- **Allergy cross-check:** explicit allergy and reaction data, with uncertain matches flagged.
- **Interaction screening:** drug-drug and, when a trusted dataset supports it, drug-condition or drug-allergy checks.
- **Dose plausibility checks:** only for validated populations and defined use cases; never assume a universal adult range applies to children, pregnancy, renal impairment, or liver impairment.
- **Reconciliation summary:** confirmed list, unresolved differences, clinician decisions, and sources.
- **Knowledge-base management:** source, version, jurisdiction, effective date, review owner, and update cadence.

## 5.5 Open-source options

### Primary candidate: [MedRecon](https://github.com/astraedus/medrecon)

- The project describes a FHIR-based medication reconciliation workflow that gathers medication records from multiple sources, identifies discrepancies, checks interactions and allergies, and generates a reconciliation report.
- Its README documents a multi-agent design and an MCP server with medication retrieval, interaction checking, allergy checks, dose validation, therapeutic alternatives, and FHIR output.
- The README describes a curated interaction set of **48 interactions** and safe-dose ranges for **18 common drugs**. This is useful for understanding its architecture, but clearly insufficient evidence for comprehensive production coverage.
- The repository's licence was not independently confirmed from a root `LICENSE` file during this audit. **Do not copy or redistribute its code until the licence is verified.**
- It uses Google ADK/A2A, MCP, Next.js, Gemini, and FHIR test infrastructure. That stack is not a direct drop-in for Diagnosia.

**Recommended reuse:** use it as an architecture and workflow reference. Evaluate the medication reconciliation algorithms and FHIR resource patterns independently. Treat every built-in interaction and dose rule as demo-level until reviewed and expanded by qualified pharmacists/clinicians.

### Supporting drug terminology: [NLM RxNav / RxNorm APIs](https://www.lhncbc.nlm.nih.gov/RxNav/APIs/index.html)

- Public APIs provide RxNorm, RxTerms, and RxClass drug concepts and classes.
- These are useful for normalising generic drug concepts and mapping drug classes.
- RxNorm is US-oriented. It does not replace an India-specific brand, formulation, packaging, availability, or regulatory product catalogue.
- Follow NLM attribution and rate-limit requirements. Cache appropriately and monitor terminology updates.

### Supporting label information: [openFDA drug label API](https://open.fda.gov/apis/drug/label/)

- Can provide searchable US drug-label data as one supporting information source.
- Do not treat it as a comprehensive interaction database or as a substitute for Indian prescribing information and locally approved clinical references.

## 5.6 India-specific considerations

- Build or license a maintained India-relevant drug catalogue and brand-to-generic mapping.
- Keep manufacturer, strength, formulation, and pack size separate from the active ingredient concept.
- Record source and effective date for all interaction and dose rules.
- Make paediatric, pregnancy, lactation, renal, and hepatic risk contexts explicit; do not generalise rules across populations.
- Do not use a language model as the authoritative source of drug interactions.

## 5.7 Suggested data and API shape

- `MedicationRecord`: patient, ingredient/product concept, strength, dose, route, frequency, start/end, status, source.
- `MedicationReconciliation`: encounter, source snapshot, differences, reviewer, final status.
- `MedicationAlert`: alert type, severity, evidence, knowledge-base version, acknowledgement/disposition.

Illustrative endpoints:

```text
GET  /api/patients/{patient_id}/medications
POST /api/addons/medguard/reconcile
POST /api/addons/medguard/check-interactions
POST /api/addons/medguard/alerts/{alert_id}/review
POST /api/addons/medguard/reconciliations/{id}/finalise
```

## 5.8 Safety and quality requirements

- Distinguish a potential interaction from a confirmed contraindication.
- Show the source and version behind each alert.
- Avoid false precision and explain when a drug cannot be matched confidently.
- Never recommend stopping, starting, or changing a medicine automatically.
- Require clinician/pharmacist review for clinically material alerts.
- Build test suites around brand/generic aliases, combination products, dose units, duplicate ingredients, and missing medication history.

**Implementation priority:** High, but only after agreeing on the drug terminology source and clinical review process. Begin with medication-list reconciliation and conservative alerts, not automated prescribing recommendations.

---

# 6. Add-on 3 — ChronicCare: Longitudinal Chronic Disease Management

## 6.1 Product overview

**ChronicCare** helps patients and care teams monitor chronic conditions across multiple visits. It provides a longitudinal view of symptoms, vitals, laboratory results, medication adherence, appointments, goals, and clinician-approved care plans.

It is not intended to independently diagnose deterioration or change treatment. Its first job is to make patient-reported and measured information visible over time and support follow-up.

## 6.2 Main users and use cases

- Patients managing diabetes, hypertension, asthma, cardiovascular disease, thyroid conditions, or other long-term conditions.
- Doctors who need a compact trend view before a follow-up appointment.
- Care coordinators who need to identify overdue tests or missed appointments.
- Caregivers who have explicitly authorised access to a patient's information.

## 6.3 Proposed workflow

1. A clinician creates or confirms a chronic-condition care plan.
2. The patient receives a simple plan with goals, measurements, and follow-up schedule.
3. The patient records symptoms, home readings, adherence, and relevant lifestyle observations.
4. Diagnosia imports approved laboratory results and observations from LabLens and other sources.
5. The dashboard plots trends, missing measurements, and patient-reported changes.
6. Rules identify when a configured threshold or overdue task needs attention.
7. The clinician reviews the trend and updates the care plan during follow-up.

## 6.4 Functional modules

- **Condition profile:** condition, date identified, managing clinician, relevant history.
- **Symptom diary:** symptom, severity, time, duration, triggers, free-text notes.
- **Vitals tracker:** blood pressure, glucose, weight, pulse, oxygen saturation, and other configured measures.
- **Lab trend charts:** results by test, unit, date, and reference range.
- **Medication adherence:** patient-reported taken/missed/held status, without assuming adherence from prescription status.
- **Care plan and goals:** clinician-approved targets, actions, due dates, and review cadence.
- **Appointment and reminder manager:** follow-up scheduling, reminders, and missed-visit workflow.
- **Patient-care-team sharing:** explicit, revocable access for clinicians and caregivers.
- **Clinician dashboard:** trends and exceptions with source attribution.

## 6.5 Open-source options

### Candidate: [Chronicly](https://github.com/kaorii-ako/chronicly)

- The repository describes a patient-oriented chronic illness tracker covering symptoms, medications, lab results, vitals, appointments, health journaling, doctor sharing, and AI-assisted features.
- Its stated stack includes Next.js, Supabase/PostgreSQL, Claude, and a separate AI fine-tuning pipeline.
- The project README states MIT licensing, but a root `LICENSE` file was not independently confirmed during this audit. **Verify the current licence and all model/data terms before reusing code.**
- It is a full application with a different frontend, authentication, and data architecture. It is not a module that can simply be mounted inside Diagnosia.

**Recommended reuse:** evaluate its symptom logging, trend visualisation, patient-sharing, and reminder UX. Reimplement the selected workflows using Diagnosia's patient, observation, and permission models.

### Strong workflow/standards resource: [AHRQ eCare Plan resources](https://www.ahrq.gov/ecareplan/resources-publications/index.html)

- AHRQ describes open-source SMART-on-FHIR apps for patients/caregivers and clinicians, including MyCarePlanner and eCarePlanner.
- The eCare Plan work focuses on shared, person-centred care plans and coordination across home, community, clinic, and research settings.
- This is a useful reference for consented sharing, patient goals, and coordinated care. Confirm source repository licences and deployment requirements before copying any component.

## 6.6 Suggested data and API shape

- `ConditionProfile`: patient, condition code, onset, status, managing team.
- `PatientReportedObservation`: patient, type, value, unit, effective time, context, source.
- `CarePlan`: goals, activities, targets, responsible parties, review date.
- `AdherenceEvent`: medication, event type, effective time, patient-reported source.

## 6.7 Safety and quality requirements

- Patient-entered measurements must be labelled as patient-entered, not clinician-verified.
- Display units and reference ranges clearly; avoid comparing incompatible units or devices.
- Threshold alerts must be configurable, clinically reviewed, and paired with clear escalation instructions.
- Provide a route to contact the care team; do not present a dashboard alert as emergency care.
- Avoid disease-specific recommendations unless a clinician-approved protocol has been selected and validated.

**Implementation priority:** Medium-high. Start with one or two conditions and a limited set of patient-entered measurements rather than launching a universal chronic-care engine.

---

# 7. Add-on 4 — LabLens: Laboratory Report Digitisation and Trends

## 7.1 Product overview

**LabLens** converts uploaded laboratory reports into structured, reviewable observations. It should preserve the original report, extract test names and values, identify units and reference ranges, and allow patients and clinicians to view results over time.

LabLens should not diagnose a condition simply because a result is outside a reference range. Reference ranges depend on laboratory, method, age, sex, pregnancy status, units, and other context.

## 7.2 Proposed workflow

1. A patient or clinician uploads a laboratory report PDF or image.
2. The system validates file type and size and scans/stores the original document securely.
3. Text extraction runs first; OCR is used only when needed for scanned pages.
4. A parser identifies report type, patient metadata, test name, value, unit, reference range, and abnormal flag if present.
5. The test is mapped to a standard concept where possible, such as LOINC, while preserving the original laboratory label.
6. Validation checks identify impossible values, missing units, ambiguous columns, and mismatched patient identifiers.
7. The user reviews uncertain fields and confirms the extracted data.
8. Accepted values are stored as observations and plotted in the patient timeline.

## 7.3 Functional modules

- **Document intake:** PDF, image, multi-page upload, file validation.
- **Text extraction and OCR:** native PDF text first, OCR fallback for image-only pages.
- **Report classifier:** identify common report categories and layouts.
- **Structured parser:** patient, collection date, test name, value, unit, reference range, lab name, method.
- **Terminology mapper:** original lab term to standard code, with mapping confidence and review status.
- **Human verification UI:** side-by-side source document and extracted fields.
- **Observation store:** accepted results with original source and provenance.
- **Trend visualisation:** only compare results when units and test concepts are compatible.
- **FHIR export:** `DiagnosticReport`, `Observation`, `Specimen`, and `DocumentReference` as appropriate.

## 7.4 Open-source options

### Primary candidate: [Lab2FHIR](https://github.com/is81/Lab2FHIR)

- Describes PDF-to-FHIR R4 conversion for laboratory and pathology reports.
- Uses FastAPI, `pdfplumber`, SQLite/SQLAlchemy, FHIR R4 resources, and LOINC mapping.
- Its repository documents multiple report parsers and a test suite; the repository contains an **MIT License**.
- Its frontend is Vue 3/Element Plus, while Diagnosia currently uses a different frontend approach.

**Recommended reuse:** strongest candidate for a technical proof of concept. Audit its parsers against the actual Indian laboratory report formats Diagnosia expects. Reuse or adapt the backend extraction/mapping patterns; do not import its separate login, database, or frontend wholesale.

### Secondary candidate: [FHIR-XTract](https://github.com/KIET7UKE/FHIR-XTract)

- Describes a configuration-driven FastAPI pipeline for healthcare PDFs, including discharge summaries and laboratory reports.
- Supports regex-based extraction and optional LLM-assisted extraction, then generates NRCeS/ABDM-oriented FHIR R4 bundles and can validate them with the FHIR Validator.
- It documents PDF text extraction, OCR fallback, `fhir.resources`, React/Vite frontend, and optional commercial LLM APIs.
- A clear licence was not independently verified in this audit. **Do not reuse code until the repository's current licence and all bundled terminology/implementation-guide terms are checked.**

**Recommended reuse:** study its configurable extraction pipeline and India-specific FHIR validation direction. Compare its parser coverage and test cases against Lab2FHIR before choosing one.

## 7.5 Suggested data and API shape

- `LabReport`: patient, laboratory, collection date, document reference, parser version, status.
- `LabResult`: report, original test name, standard code, value, unit, reference range, flag, confidence, review status.

Illustrative endpoints:

```text
POST /api/addons/lablens/reports
GET  /api/addons/lablens/reports/{report_id}/extraction
PATCH /api/addons/lablens/results/{result_id}
POST /api/addons/lablens/reports/{report_id}/confirm
GET  /api/patients/{patient_id}/lab-trends
```

## 7.6 Safety and quality requirements

- Always retain an immutable original report reference.
- Never discard the laboratory's original test label, units, or reference range after mapping.
- Display extraction confidence and require review of ambiguous fields.
- Do not combine values when units differ or the analyte/method is not comparable.
- Validate decimal separators, negative values, high/low flags, page tables, and patient identifiers.
- Use representative, consented, de-identified test documents for development; do not ship test samples containing real patient data.

**Implementation priority:** High. It has a clear bounded scope and one of the best-aligned FastAPI open-source starting points.

---

# 8. Add-on 5 — DischargeCare: Hospital Discharge and Follow-up Coordination

## 8.1 Product overview

**DischargeCare** coordinates the transition from hospital to home or another care setting. It turns a discharge plan into clear tasks for the patient, caregiver, and care team, and makes it easier to confirm whether follow-up actions were completed.

It is primarily a workflow and coordination product, not an automated discharge-decision engine.

## 8.2 Proposed workflow

1. A clinician or authorised staff member creates a discharge episode.
2. The system records the discharge summary, medication reconciliation, pending results, follow-up appointments, and patient instructions.
3. Tasks are assigned to the patient, caregiver, clinic, or other responsible party.
4. The patient receives a plain-language discharge checklist and contact instructions.
5. Reminders are sent for medication review, appointments, tests, wound review, or other clinician-defined actions.
6. The patient or care team marks tasks complete, missed, or blocked.
7. Unresolved tasks are escalated to the responsible care team.
8. The discharge episode closes only when the required workflow criteria are met or a clinician records an exception.

## 8.3 Functional modules

- **Discharge episode:** admission/discharge dates, destination, responsible clinician, linked encounter.
- **Discharge summary:** clinician-approved diagnosis/problem list, procedures, pending results, instructions.
- **Medication handoff:** link to MedGuard reconciliation and the confirmed medication list.
- **Patient checklist:** medicines, appointments, tests, warning signs, self-care instructions.
- **Task orchestration:** ownership, deadlines, dependency, priority, status, escalation.
- **Follow-up scheduler:** primary-care review, specialist visit, lab test, wound review, or other configured follow-up.
- **Caregiver access:** permission-based task support.
- **Outcome dashboard:** completed, overdue, blocked, and escalated tasks.

## 8.4 Open-source options

### Primary workflow reference: [Microsoft Patient Discharge Planning](https://github.com/microsoft/patient-discharge-planning)

- Microsoft describes it as a sample solution for discharge planning designed to work as part of Microsoft Cloud for Healthcare.
- The repository includes workflow ideas for aggregating relevant information, prioritising discharge barriers, secure asynchronous communication, task management, and reporting.
- The repository contains an **MIT License**.
- It is a sample built for a Microsoft healthcare environment, not a standalone Diagnosia module. Assess Azure/Microsoft dependencies before considering code reuse.

**Recommended reuse:** study its task coordination and discharge barrier workflows. Reimplement the relevant pieces using Diagnosia's shared `Task`, `Encounter`, `ClinicalDocument`, `CareTeam`, and `Notification` records.

### Supporting shared-care pattern: [AHRQ eCare Plan resources](https://www.ahrq.gov/ecareplan/resources-publications/index.html)

Use as a reference for patient/caregiver/clinician shared care plans. Confirm each app's source and licence before directly reusing code.

## 8.5 Suggested data and API shape

- `DischargeEpisode`: patient, encounter, discharge date, destination, status.
- `DischargeInstruction`: topic, text, language, author, approval state.
- `DischargeTask`: linked task, owner, due date, completion evidence.
- `FollowUpPlan`: appointment/test/referral requirements and responsible organisation.

## 8.6 Safety and quality requirements

- Only a qualified clinician or authorised hospital workflow should approve the discharge summary and instructions.
- Distinguish pending test results from final results.
- Do not generate personalised warning signs or medicine changes without approved source content and review.
- Provide emergency contact information appropriate to the provider and geography.
- Escalate missed high-priority tasks to a named team rather than relying only on automated reminders.

**Implementation priority:** Medium. It becomes more valuable after patient encounters, medication reconciliation, referrals, and notifications exist.

---

# 9. Add-on 6 — MaternaCare: Maternal and Postnatal Care Coordination

## 9.1 Product overview

**MaternaCare** is a specialised maternal-care pathway that supports antenatal registration, scheduled check-ups, patient education, maternal observations, birth planning, and postnatal follow-up for the mother and newborn.

The goal is continuity, visibility, and timely escalation—not autonomous obstetric risk diagnosis. Clinical protocols must be defined and approved by qualified obstetric professionals and aligned with the deployment setting.

## 9.2 Main users and use cases

- Pregnant patients and authorised caregivers.
- Obstetricians, gynaecologists, midwives, and nurses.
- Community health workers and maternal-care coordinators.
- Clinics coordinating postnatal review and newborn follow-up.

## 9.3 Proposed workflow

1. Create a maternal-care episode with the patient's consent and verified identity.
2. Record pregnancy-related history and clinician-confirmed gestational details.
3. Create a schedule of antenatal visits and clinician-defined investigations.
4. Record observations and test results with date, unit, source, and reviewer.
5. Deliver patient education and appointment reminders in the patient's selected language.
6. Allow clinicians to record risk factors and follow approved escalation pathways.
7. Record delivery details and the care transition to postnatal follow-up.
8. Track maternal and newborn postnatal tasks, visits, and referrals.

## 9.4 Functional modules

- **Maternal profile:** pregnancy episode, estimated dates, gravida/para history where clinically appropriate, care provider.
- **Antenatal schedule:** visit plan, due investigations, missed-visit follow-up.
- **Maternal observations:** blood pressure, weight, haemoglobin, glucose, and other clinician-selected observations.
- **Clinical risk register:** clinician-confirmed conditions and protocol-based review flags.
- **Education library:** reviewed content, language variants, version, and publisher.
- **Birth plan:** facility, preferred contacts, transport and referral arrangements where applicable.
- **Delivery record:** date/time, facility, outcome and linked newborn record as authorised.
- **Postnatal care:** maternal recovery check, newborn visits, feeding support, and referral tasks.
- **Referral escalation:** urgent referral status, receiving facility, acknowledgement, and outcome.

## 9.5 Open-source options

### Broad EHR platform: [OpenMRS](https://github.com/openmrs/openmrs-core)

- OpenMRS is an extensible open-source medical record platform with a modular architecture.
- The `openmrs-core` repository declares **MPL-2.0**.
- The OpenMRS ecosystem includes patient chart, patient management, web services, and FHIR-related modules.
- It is a broad EHR platform, not a ready-made MaternaCare module. Integrating the entire platform into Diagnosia would add substantial architectural weight.

**Recommended reuse:** use its modular clinical workflow concepts and inspect relevant community modules or forms. Prefer adapting a specific form/workflow idea rather than deploying OpenMRS as a second patient database.

### Community-health platform: [Agni by Lattice](https://www.thelattice.in/projects/agni)

- Agni describes itself as open-source, mobile-first software for primary and community healthcare.
- It is FHIR R4-based, uses HAPI FHIR for storage, and supports offline-first workflows and household/beneficiary tracking.
- Its public repositories include [agni-main](https://github.com/LatticeInnovations/agni-main), [agni-android](https://github.com/LatticeInnovations/agni-android), and [agni-facade](https://github.com/LatticeInnovations/agni-facade).
- The project describes its codebase and design documentation as public domain; verify the exact terms in each repository and for every dependency before reusing.

**Recommended reuse:** examine offline data capture, community follow-up, household relationships, and FHIR integration patterns. It is not a plug-in maternal-care application.

## 9.6 India-specific workflow considerations

Review the applicable national and state maternal-health programmes, records, visit schedules, referral pathways, and approved education material before defining forms or alerts. Do not invent a schedule or treat a generic international workflow as an India-specific protocol. Clinical staff must validate terminology, age/gestation rules, danger-sign pathways, and escalation ownership.

## 9.7 Safety and quality requirements

- Maternal and newborn records need carefully scoped access and explicit relationship permissions.
- Distinguish patient-reported symptoms from clinician-confirmed conditions.
- Any danger-sign or urgent escalation flow must be clinically approved, tested, and clearly actionable.
- Do not use generative AI to infer gestational age, diagnose pregnancy complications, or recommend treatment.
- Ensure reliable escalation even when reminders fail or the patient has no smartphone/data connection.

**Implementation priority:** Later phase. High potential value, but it needs maternal-health domain partners, local protocol review, and a robust referral pathway before launch.

---

# 10. Add-on 7 — AMR Guard: Antimicrobial Stewardship and Resistance Surveillance

## 10.1 Product overview

**AMR Guard** is an institutional tool for antimicrobial stewardship and resistance surveillance. It can help a hospital review antibiotic use, microbiology results, local antibiograms, culture follow-up, and stewardship interventions.

This is the highest-risk module in the proposed list. It must not operate as a consumer-facing chatbot that chooses antibiotics. Antibiotic selection and changes require qualified clinicians and microbiology/infectious-disease expertise, using local susceptibility data and approved guidelines.

## 10.2 Main users and use cases

- Hospital antimicrobial stewardship teams.
- Infectious-disease clinicians and pharmacists.
- Microbiology laboratories.
- Hospital epidemiology and quality teams.
- Institutional leaders monitoring antibiotic consumption and resistance trends.

## 10.3 Proposed workflow

1. Import or enter microbiology results, organism identification, specimen source, and antimicrobial susceptibility results.
2. Validate organism names, antibiotic codes, susceptibility categories, dates, and laboratory provenance.
3. Link results to the relevant encounter and antimicrobial exposure where permitted.
4. Generate aggregate facility-level resistance summaries and antibiograms using an approved methodology.
5. Provide a review queue for culture results that need clinician acknowledgement.
6. Allow stewardship staff to record interventions such as review requested, de-escalation considered, duration review, or guideline deviation discussion.
7. Report aggregate trends and data-quality issues to institutional users.

## 10.4 Functional modules

- **Microbiology import:** structured lab feeds or controlled CSV imports.
- **AST data validation:** organism, specimen, antibiotic, MIC/zone, interpretation, standard, and test date.
- **Antibiogram builder:** aggregate susceptibility summaries with an explicit methodology and minimum sample thresholds.
- **Culture follow-up queue:** pending, positive, reviewed, acknowledged, and actioned states.
- **Antibiotic exposure view:** prescribed drug, route, start/stop, indication, duration, and encounter.
- **Stewardship review records:** reviewer, recommendation, rationale, acceptance/rejection, follow-up.
- **Surveillance dashboards:** organism/resistance trends and data completeness.
- **Guideline registry:** versioned local/national guidelines and responsible clinical owners.

## 10.5 Open-source options and official resources

### Prototype candidate: [Foundation AMR Guard](https://github.com/OliWoods-Org/foundation-amr-guard)

- Its repository describes narrow-spectrum stewardship recommendations and antibiogram generation.
- It declares **AGPL-3.0**. This is a strong copyleft licence; network use of a modified covered program can trigger source-offer obligations. Obtain legal review before incorporating it into a proprietary hosted product.
- It is a prototype and explicitly seeks clinical, microbiology, epidemiology, and stewardship expertise. Treat it as research/reference material, not a validated clinical system.

### Data analysis library: [AMR R package](https://github.com/msberends/AMR)

- An open-source R package for antimicrobial resistance data analysis and microbiological data handling.
- The project identifies its licence as **GPL-2.0**.
- It may be useful for an isolated analytics pipeline or research validation, but it is not a FastAPI clinical application and carries copyleft obligations that need review.

### India-specific operational reference: [NCDC Antimicrobial Resistance Programme](https://www.ncdc.mohfw.gov.in/includes/About/CentresAndDivision/amr.php)

- NCDC publishes AMR surveillance SOPs, WHONET data-entry/reporting guidance, and facility-level antibiogram tools.
- These are important operational references for Indian surveillance context. They are not a substitute for building validated software or checking the current version of each SOP.

## 10.6 Suggested data and API shape

- `MicrobiologyResult`: patient, encounter, specimen, organism, antibiotic, result, method, lab, effective date.
- `AntibiogramRun`: facility, period, methodology, inclusion criteria, output, reviewer.
- `StewardshipReview`: encounter, reviewer, finding, recommendation, disposition, evidence.

## 10.7 Safety and quality requirements

- Never generate an antibiotic prescription or dosage automatically.
- Do not recommend narrow-spectrum therapy without organism, site, susceptibility, patient context, and approved local guidance.
- Clearly separate aggregate surveillance from individual-patient treatment decisions.
- Record the testing standard and version used to interpret susceptibility results.
- Validate sample sizes, duplicate isolates, mixed cultures, and laboratory quality before building antibiograms.
- Require microbiologist and stewardship-team approval of methods and alert logic.

**Implementation priority:** Last / specialist phase. Build only with a hospital or microbiology partner and an explicit clinical governance plan.

---

# 11. Add-on 8 — ReferralLoop: Specialist Referral and Back-Referral Tracking

## 11.1 Product overview

**ReferralLoop** tracks a referral from the moment a clinician requests specialist review until the specialist visit occurs and the outcome returns to the referring team.

The core problem is administrative continuity: referrals can be delayed, lost, duplicated, or completed without the result returning to the original clinician. ReferralLoop should initially solve tracking and coordination, not decide whether a patient needs a specialist.

## 11.2 Main users and use cases

- Primary-care clinicians and referring doctors.
- Specialist clinics and receiving hospitals.
- Referral coordinators and front-desk teams.
- Patients and caregivers who need appointment and preparation information.

## 11.3 Proposed workflow

1. A clinician creates a referral linked to the patient and encounter.
2. The referral contains the reason, priority, requested specialty, relevant documents, and clinical context.
3. A coordinator or receiving service acknowledges the referral.
4. The system tracks triage, appointment offered, appointment booked, rescheduled, attended, or declined.
5. The specialist records the encounter and uploads a consult note or outcome.
6. The result is sent back to the referring clinician and attached to the patient timeline.
7. Unacknowledged, overdue, or incomplete referrals enter an exception queue.
8. The referral closes only after the expected outcome is recorded or a documented reason for closure exists.

## 11.4 Functional modules

- **Referral creation:** specialty, reason, clinical question, requested priority, destination.
- **Referral package:** relevant notes, lab results, images, prescriptions, and consented documents.
- **Routing:** clinic/hospital directory, specialty, location, service availability, manual assignment.
- **Acknowledgement and triage:** received, accepted, redirected, needs-more-information, declined.
- **Appointment tracking:** offered, booked, rescheduled, cancelled, attended, no-show.
- **Specialist outcome:** consult note, diagnosis/assessment, recommendations, return-to-primary-care plan.
- **Back-referral:** information returned to the referring clinician, acknowledgement, next action.
- **Exception management:** silent referral, overdue appointment, missing report, duplicate referral.
- **Patient communication:** status notifications and instructions, subject to consent and contact preferences.

## 11.5 Open-source option

### Workflow and FHIR reference: [Healthtech Referral Management Toolkit](https://github.com/Sofeso/healthtech-referral-management-toolkit)

- Provides a referral workflow map, FHIR R4 use cases, JSON examples, test scenarios, and a governance checklist.
- It maps referral stages to FHIR resources such as `ServiceRequest`, `Appointment`, `Encounter`, and `DocumentReference`.
- Its README describes it as a generic illustrative pattern, not a validated clinical/regulatory artifact. It notes that sample terminology codes are placeholders and must be validated.
- Its licence is **CC BY 4.0**, which permits reuse with attribution; retain the required attribution and review the licence file.
- It is a workflow/documentation toolkit, not a ready-made web application.

**Recommended reuse:** this is the best starting point for ReferralLoop's workflow and acceptance tests. Implement the workflow natively in Diagnosia using shared patient and task records.

### Supporting platform option: OpenMRS FHIR module

OpenMRS maintains a FHIR module in its ecosystem. It may be useful as an interoperability reference or external-system integration option, but adding OpenMRS solely to obtain referral tracking would likely be excessive for the current Diagnosia architecture.

## 11.6 Suggested data and API shape

- `Referral`: patient, referring practitioner, receiving organisation, specialty, reason, priority, status.
- `ReferralEvent`: status transition, actor, timestamp, note, source.
- `ReferralAppointment`: appointment, scheduled time, attendance outcome.
- `ReferralOutcome`: consult note, recommendations, follow-up owner, return status.

Illustrative endpoints:

```text
POST /api/addons/referrals
GET  /api/addons/referrals/{referral_id}
POST /api/addons/referrals/{referral_id}/acknowledge
PATCH /api/addons/referrals/{referral_id}/status
POST /api/addons/referrals/{referral_id}/outcome
GET  /api/patients/{patient_id}/referrals
```

## 11.7 Safety and quality requirements

- Priority categories and escalation times must be defined by the participating clinical service.
- Do not infer clinical urgency solely from free-text AI classification.
- Make receiving-service acknowledgement visible.
- Maintain a clear record of who changed priority or routing and why.
- Avoid duplicate referrals and ensure that cancellation/closure is explicit.
- Provide a manual fallback for phone/fax/offline referral workflows where needed.

**Implementation priority:** Medium-high. It is a strong platform feature once Diagnosia has shared patient/encounter/document records.

---

# 12. Open-source shortlist and licence review matrix

The status below reflects the repository pages and documentation checked while preparing this document. It is not a legal opinion and can change when maintainers update a repository.

| Add-on | Candidate / resource | What it can contribute | Licence / status observed | Practical recommendation |
|---|---|---|---|---|
| ClinScribe | [OpenScribe](https://github.com/sammargolis/OpenScribe) | Encounter recording, transcription, draft clinical notes | MIT confirmed; project states it is not HIPAA compliant yet | Best codebase to inspect first; adapt selected pipeline only |
| MedGuard | [MedRecon](https://github.com/astraedus/medrecon) | FHIR medication aggregation, discrepancy workflow, interaction/allergy checks | Licence not independently confirmed in this audit; small curated ruleset described | Architecture reference only until licence and clinical coverage are audited |
| MedGuard | [RxNav / RxNorm](https://www.lhncbc.nlm.nih.gov/RxNav/APIs/index.html) | Drug concepts and class terminology | Public NLM APIs; follow attribution and terms | Use as one terminology source, not an India drug catalogue replacement |
| ChronicCare | [Chronicly](https://github.com/kaorii-ako/chronicly) | Symptom logs, vitals, medications, lab trends, sharing | README states MIT; root licence file not independently confirmed | UX/reference; confirm licence before copying code |
| ChronicCare / DischargeCare | [AHRQ eCare Plan](https://www.ahrq.gov/ecareplan/resources-publications/index.html) | Open-source SMART-on-FHIR care planning apps and workflows | AHRQ page says products are open-source; confirm each repository's licence | Strong care-plan workflow reference |
| LabLens | [Lab2FHIR](https://github.com/is81/Lab2FHIR) | PDF lab parsing, LOINC mapping, FHIR R4, FastAPI backend | MIT confirmed | Best technical starting point for a bounded proof of concept |
| LabLens | [FHIR-XTract](https://github.com/KIET7UKE/FHIR-XTract) | Configurable PDF extraction and NRCeS/ABDM FHIR bundle validation | Licence not independently verified in this audit | Compare parsers and validation approach; do not copy until licence is confirmed |
| DischargeCare | [Microsoft Patient Discharge Planning](https://github.com/microsoft/patient-discharge-planning) | Discharge barriers, task coordination, messaging, reporting | MIT confirmed; depends on Microsoft healthcare ecosystem | Workflow reference; selectively adapt concepts |
| MaternaCare | [OpenMRS](https://github.com/openmrs/openmrs-core) | Extensible EHR and clinical workflow ecosystem | MPL-2.0 observed for openmrs-core | Study modules/forms; avoid deploying a second EHR without a strong reason |
| MaternaCare | [Agni by Lattice](https://www.thelattice.in/projects/agni) | Offline-first primary/community healthcare, FHIR R4, household/beneficiary workflows | Project describes code/design as public domain; verify each repo's terms | Study community-health workflows and offline sync |
| AMR Guard | [Foundation AMR Guard](https://github.com/OliWoods-Org/foundation-amr-guard) | Stewardship/antibiogram prototype | AGPL-3.0 confirmed | Legal and clinical review required; not production-ready |
| AMR Guard | [AMR R package](https://github.com/msberends/AMR) | AMR data analysis and microbiology methods | GPL-2.0 as stated by project | Consider only for isolated research/analytics after licence review |
| AMR Guard | [NCDC AMR resources](https://www.ncdc.mohfw.gov.in/includes/About/CentresAndDivision/amr.php) | Indian surveillance SOPs and facility antibiogram references | Official guidance/tools; not necessarily reusable source code | Use as local operating reference and validate current versions |
| ReferralLoop | [Healthtech Referral Management Toolkit](https://github.com/Sofeso/healthtech-referral-management-toolkit) | Referral workflow, FHIR examples, tests, governance checklist | CC BY 4.0 stated in README | Best workflow/specification starting point; implement natively |

### 12.1 Licence notes

- **MIT:** generally permissive, but preserve copyright and licence notices and review third-party components.
- **MPL-2.0:** file-level copyleft; modifications to MPL-covered files have source-disclosure obligations when distributed under the licence. Get legal advice for the intended distribution model.
- **GPL-2.0 / AGPL-3.0:** stronger copyleft obligations. AGPL is specifically relevant to network-served modified software. Do not assume the code can be embedded in a proprietary hosted product without consequence.
- **CC BY 4.0:** applies to creative/documentation material, not automatically to software code in the same repository. Preserve attribution and inspect individual assets and examples.
- **No confirmed licence:** do not copy code simply because a GitHub repository is public. Public visibility is not a licence to reuse.
- **Models and datasets:** code licence does not grant rights to model weights, training data, terminology databases, API content, or patient datasets. Audit each one separately.

---

# 13. Recommended implementation order

The eight tools should not be built simultaneously. The order below balances user value, architecture reuse, implementation effort, and clinical risk.

## Phase 0 — Shared foundation

Before expanding the product, implement or validate:

- Patient and practitioner identity separated from login users.
- Encounter and patient timeline.
- Shared document storage with access controls.
- Clinical provenance and review states.
- Consent and audit-event records.
- Role and patient-level access checks.
- Task and notification primitives.
- FHIR R4 export/validation approach and India-specific profile review.

## Phase 1 — High-value clinical workflow additions

1. **LabLens:** start with one or two common report types, source-document verification, and human confirmation.
2. **ClinScribe:** reuse the existing voice/LLM abstractions where suitable; create clinician-side note drafting and approval.
3. **MedGuard:** start with medication list aggregation and reconciliation; add only a small, clinically reviewed alert set initially.

## Phase 2 — Continuity and coordination

4. **ReferralLoop:** referral creation, acknowledgement, appointment state, and returned consult note.
5. **ChronicCare:** symptom/vital tracking and a clinician-approved care plan for a narrow condition set.
6. **DischargeCare:** discharge tasks, medication handoff, pending-result follow-up, and escalation.

## Phase 3 — Specialised programmes

7. **MaternaCare:** proceed with obstetric and community-health partners, approved local workflows, and strong escalation controls.
8. **AMR Guard:** proceed only with a hospital microbiology/stewardship partner, validated antibiogram methodology, and legal review of AGPL components.

---

# 14. Cross-module integration examples

### 14.1 A patient visit

1. DiagnoFast collects the patient's initial complaint.
2. The doctor opens the same encounter and uses ClinScribe for consultation documentation.
3. LabLens imports and structures relevant previous laboratory reports.
4. MedGuard presents the current medication list and unresolved discrepancies.
5. The clinician reviews and signs the note and confirms the medication record.
6. A referral or ChronicCare plan is created if needed.

### 14.2 A hospital discharge

1. The inpatient encounter is closed by an authorised hospital user.
2. ClinScribe may assist with the discharge documentation draft, subject to clinician review.
3. MedGuard reconciles medicines across admission, inpatient, and discharge sources.
4. LabLens structures pending or recent test reports.
5. DischargeCare creates patient/caregiver tasks and follow-up reminders.
6. ReferralLoop tracks specialist follow-up and returns the consult outcome.
7. ChronicCare receives only the clinician-approved plan and observations.

### 14.3 Maternal care pathway

1. MaternaCare maintains the maternal-care episode and appointment schedule.
2. LabLens attaches verified laboratory observations.
3. MedGuard supports medication-list review where clinically appropriate.
4. ReferralLoop tracks a referral to a higher-level facility or specialist.
5. DischargeCare can support post-delivery discharge tasks.
6. ChronicCare may provide longitudinal monitoring only where the selected workflow is clinically appropriate.

---

# 15. Non-functional requirements for every add-on

## Security and privacy

- Enforce role-based and patient/encounter-level authorisation on every endpoint.
- Use secure, short-lived sessions and CSRF protections where cookie-based authentication requires them.
- Encrypt data in transit and at rest; protect backups and object storage.
- Keep secrets out of source control and browser bundles.
- Restrict staff access by organisation and care relationship.
- Log sensitive reads, exports, edits, and administrative access.
- Make consent scope and revocation enforceable, not merely a checkbox in the UI.
- Define data retention and deletion policies for audio, documents, derived text, and model-provider requests.

## Reliability

- Use background jobs for OCR, transcription, PDF parsing, and notifications.
- Make long-running jobs retryable and idempotent.
- Store processing state and error reason; do not silently drop uploads.
- Provide graceful failure when external AI or terminology APIs are unavailable.
- Maintain a human/manual path for clinically important workflows.

## AI governance

- Version prompts, model identifiers, parser versions, and extraction configuration.
- Record whether information is generated, extracted, patient-entered, or clinician-entered.
- Evaluate models on de-identified, representative, consented datasets.
- Measure clinically meaningful errors, not just generic text similarity.
- Test uncertainty, refusal, hallucination, missing data, and conflicting-source scenarios.
- Keep a human approval step before clinical records are finalised or actions are taken.

## Testing

Every module should have:

- Unit tests for domain logic and state transitions.
- API tests for permissions and invalid input.
- Integration tests for document storage, AI/terminology providers, and database transactions.
- Regression tests for extraction and parsing using synthetic or properly de-identified samples.
- Audit tests verifying that access and changes are recorded.
- Accessibility and mobile-viewport checks for patient-facing workflows.
- Failure tests for network loss, provider timeout, duplicate uploads, and retry behaviour.

---

# 16. What Claude Code should do before implementation

Use this document as the product brief, but do not start by cloning every repository into Diagnosia. The recommended implementation process is:

1. **Inspect the actual Diagnosia codebase.** Confirm current routes, models, migrations, frontend structure, auth, storage, AI clients, tests, and deployment assumptions.
2. **Create an integration audit.** For each candidate, inspect the exact commit, README, licence, dependencies, install steps, tests, security notes, data models, API surface, and known limitations.
3. **Build a compatibility matrix.** Identify which parts can be reused as code, which are useful only as workflow references, and which should be rebuilt natively.
4. **Resolve licences first.** Do not copy code from MedRecon, Chronicly, or FHIR-XTract until the licence is confirmed. Review AGPL/GPL/MPL obligations before any incorporation.
5. **Design shared records first.** Agree on patient, encounter, observation, document, medication, task, consent, provenance, and audit structures before building each add-on.
6. **Implement one vertical slice at a time.** A vertical slice includes backend, UI, persistence, permission checks, audit events, tests, and failure handling.
7. **Use synthetic data initially.** Do not copy real patient information into third-party repositories, public test servers, issue trackers, or external AI prompts.
8. **Require a clinical review gate.** No module should be marketed or used as a validated clinical decision-support product without appropriate clinical validation and regulatory assessment.

### Expected engineering deliverables

For each add-on, create:

- `README.md` — purpose, setup, workflow, limitations.
- `PRODUCT_SPEC.md` — user stories, acceptance criteria, roles, state transitions.
- `ARCHITECTURE.md` — services, data flow, API and integration boundaries.
- `DATA_MODEL.md` — tables, relationships, indexes, provenance.
- `SAFETY_AND_VALIDATION.md` — clinical limits, review requirements, validation plan.
- Tests for domain logic, API permissions, workflow transitions, and failures.

Also maintain platform-level files:

- `OPEN_SOURCE_AUDIT.md`
- `LICENCE_AND_DEPENDENCY_AUDIT.md`
- `FHIR_MAPPING.md`
- `CLINICAL_GOVERNANCE.md`
- `DATA_RETENTION_AND_CONSENT.md`

---

# 17. Final recommendations

1. **Do not integrate all eight as separate external apps.** Build them as Diagnosia modules around one patient timeline and shared platform services.
2. **Start with LabLens, ClinScribe, and MedGuard.** They add concrete clinical workflows and connect naturally to the existing prescription and voice-intake capabilities.
3. **Use ReferralLoop and ChronicCare to create continuity.** They turn individual tools into a patient journey rather than a set of disconnected utilities.
4. **Add DischargeCare after the shared task and notification system exists.** It depends on the other modules being able to provide reliable clinical information.
5. **Treat MaternaCare and AMR Guard as specialised programmes.** They need domain experts, locally approved workflows, validation, and partnerships.
6. **Prefer MIT or similarly permissive code where possible, but still audit every dependency.** Open source does not mean free APIs, production readiness, clinical validity, or automatic permission to use datasets and model weights.
7. **Keep clinicians in control.** AI should draft, extract, organise, and flag. It must not silently diagnose, prescribe, sign, or make high-impact clinical decisions.

The intended end state is a connected Diagnosia platform in which patient information is entered once, attributed to its source, reviewed by the right person, and reused safely across consultation, medication, laboratory, chronic-care, referral, discharge, and specialist workflows.

---

## 18. Reference links

### Repositories and projects

- [OpenScribe](https://github.com/sammargolis/OpenScribe)
- [MedRecon](https://github.com/astraedus/medrecon)
- [Chronicly](https://github.com/kaorii-ako/chronicly)
- [Lab2FHIR](https://github.com/is81/Lab2FHIR)
- [FHIR-XTract](https://github.com/KIET7UKE/FHIR-XTract)
- [Microsoft Patient Discharge Planning](https://github.com/microsoft/patient-discharge-planning)
- [OpenMRS Core](https://github.com/openmrs/openmrs-core)
- [Agni by Lattice](https://www.thelattice.in/projects/agni)
- [Agni Android](https://github.com/LatticeInnovations/agni-android)
- [Agni Facade](https://github.com/LatticeInnovations/agni-facade)
- [Foundation AMR Guard](https://github.com/OliWoods-Org/foundation-amr-guard)
- [AMR R package](https://github.com/msberends/AMR)
- [Healthtech Referral Management Toolkit](https://github.com/Sofeso/healthtech-referral-management-toolkit)

### Standards and official resources

- [ABDM FHIR Implementation Guide](https://www.nrces.in/ndhm/fhir/r4/)
- [ABDM FHIR Implementation Guide downloads](https://www.nrces.in/ndhm/fhir/r4/downloads.html)
- [ABDM FHIR v7 preview build](https://www.nrces.in/preview/ndhm/fhir/r4/index.html)
- [AHRQ eCare Plan resources](https://www.ahrq.gov/ecareplan/resources-publications/index.html)
- [NLM RxNav APIs](https://www.lhncbc.nlm.nih.gov/RxNav/APIs/index.html)
- [openFDA Drug Label API](https://open.fda.gov/apis/drug/label/)
- [NCDC Antimicrobial Resistance Programme](https://www.ncdc.mohfw.gov.in/includes/About/CentresAndDivision/amr.php)

---

**End of document.**
