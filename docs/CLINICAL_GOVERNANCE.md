# Clinical governance

All tools, including the original DiagnoFast, Prescription Analyzer and Skin Cancer Detection, are **pilot software, not validated for patient care**. No
clinician, laboratory professional, lawyer or regulator
has reviewed anything here. Use synthetic data only.

The compliance section below is an assessment for qualified counsel and compliance
reviewers. It is not legal advice or a determination, and software can't certify itself.

## What blocks real patient use

Each gate is a named signoff in `backend/safety.py`. `python -m backend.operator_cli
readiness` reports them. The application refuses to start if real data is requested
while any is missing. A signoff records a person's decision, so entering one without
that decision would defeat the control.

| Signoff item | Who must give it | Blocks |
|---|---|---|
| `clinical_safety_lead` | Accountable clinician | All |
| `laboratory_mapping_review` | Laboratory professional | LabLens, FHIR export |
| `cdsco_classification` | Regulatory adviser | All device-candidate functions |
| `dpdp_assessment` | Legal adviser | All |
| `groq_agreement` | Legal, with Groq, and zero retention switched on | ClinScribe, LabLens, Prescription Analyzer, speech |
| `gemini_agreement` | Legal, with Google, on a plan that doesn't train on content | Every AI tool, whenever a Gemini key is set |
| `infrastructure_encryption` | Security reviewer | All |
| `security_penetration_test` | Independent tester | All |
| `incident_response` | Named clinical and data owners | All |
| `loinc_notice` | Legal adviser | Any distribution using LOINC codes |

## Known limits

- **Staff verification proves only what a person checked.** No code verifies a
  medical registration. See `STAFF_VERIFICATION_WORKFLOW.md`.
- **Every clinician on a care team has full access.** There are no finer roles.
- **The ClinScribe verifier is a text check.** It catches many errors, not all, and can
  only compare by hand in Hindi. Speaker labels are manual.
- **LabLens was measured on synthetic layouts only.** See
  `EXTERNAL_INTEGRATION_VALIDATION.md`.
- **Trends can't detect a change of method between laboratories.**
- **Emergency numbers 112 and 108 are shown.** Confirm these for each deployment.
- **SMS and voice reminders aren't connected.** A person must make those calls.
- **Whisper can invent words from silence.** Segments it marks as probably not speech are dropped,
  but a clinician must still check transcripts.
- **Hindi reading aloud uses the device's voice,** so quality varies, and some devices have none.
- **Existing Skin Cancer Detection is outside this sprint**, but see the CDSCO table.

## Rules built into the code, with tests

- AI drafts and extracts. It never signs, confirms, diagnoses or prescribes.
- Unreviewed LOINC codes are left out of exports.
- Nothing AI-produced enters the record without a person's review.
- A reminder never replaces a clinical escalation.

## Compliance readiness assessment

Checked on 31 October 2026. Where a primary source could not be read, that is stated.

### A. CDSCO: medical device software

**Source read:** CDSCO, *Guidance Document on Medical Device Software*, Doc No.
CDSCO/MD/GD/MDSW/01/2026, from the
[CDSCO site](https://cdsco.gov.in/opencms/export/sites/CDSCO_WEB/Pdf-documents/Guidance-document-on-Medical-Device-Software-under-MDR-2017.pdf).
A [secondary source](https://meddeviceguide.com/blog/india-cdsco-medical-device-software-mdsw-guidance-2026-guide)
reports a release date of 21 July 2026. The document's own notice says it creates
public awareness and is "not meant to be used for legal or professional purposes", so
the Medical Devices Rules, 2017 still govern.

What it says that matters here:

- Section 5.2 lists software **not** covered, including software that performs
  "transfer, storage, archive data, convert, format, communication, simple search",
  and HIS/CIS for admissions, appointments, billing and storing patient information.
- Its note adds that an HIS or LIS with additional functions for medical purposes,
  such as "quantification of physiological parameters for clinical decision-making",
  "may be considered as a medical device".
- Section 5.1 gives covered examples including an AI/ML tool for "triage, and/or
  screening of cancer lesions" (Example 8), a platform tracking chronic conditions with
  analytics and interventions (Example 9), and remote coaching and tracking for chronic
  disease (Example 10).
- Non-device functions in a system "may not be regulated", but the manufacturer should
  explain their impact on the device function.
- Table 2 classes standalone software by the condition (critical, serious, non-serious)
  against the significance of its information (treat or diagnose, drive, inform), from
  class A to D. Software for non-clinical users in a serious condition, without
  professional support, "may be considered" as used in a critical one.

**Preliminary view for the regulatory adviser. None of these is a determination.**

| Function | Why it needs assessing | Early view |
|---|---|---|
| Skin Cancer Detection (existing) | Closely resembles Example 8 | **Strong candidate.** Highest priority, despite being outside this sprint |
| ClinScribe drafting | AI summarising clinical content | Needs assessment. Documentation only is arguably closer to 5.2 |
| LabLens extraction and range display | Record-keeping, but shows above or below range | Needs assessment against the LIS note |
| Prescription Analyzer (existing) | Explains medicines to patients | Needs assessment |
| DischargeCare, ReferralLoop, Reminders | Workflow and communication | Possibly outside, if the intended use stays administrative |

**Evidence needed:** an intended-use statement per function, risk classification,
quality system (the guidance lists IS/ISO/IEC 62304 and others), clinical evaluation,
software bill of materials, cybersecurity file, and an algorithm change protocol where
AI applies. Wording on this site and in the app counts toward intended use.

### B. DPDP Act 2023 and Rules 2025

**Primary Gazette text was not read in this review.** Dates come from secondary
sources: [DLA Piper](https://www.dlapiperdataprotection.com/?t=law&c=IN),
[dcomply reference](https://dpdpa.dcomply.in/rules/) (self-reported as verified 8
September 2026) and [ASTRA Ready](https://www.astraready.com/insights/dpdp-rules-phased-commencement).

- Rules G.S.R. 846(E) and commencement G.S.R. 843(E), dated 13 November 2025. Some
  sources give 14 November, the press announcement.
- Rules 1, 2 and 17 to 21 at once. Rule 4 (consent managers) after one year, around
  13 to 14 November 2026. The main obligations after 18 months, around 13 to 14 May 2027.
- **Urgent check for counsel:** sources mention a floated proposal to shorten the
  18 months to 12, which would bring the main date to about 13 November 2026, two weeks
  from this review. It was reported as not notified. This review couldn't confirm the
  current position.
- Until then, sources say the IT Act and its privacy rules continue to apply.

| Topic | Today | Gap for counsel |
|---|---|---|
| Personal data held | Identity, contact, health, audio-derived text, documents, staff credentials | Formal data inventory |
| Notice | Draft `privacy.html`, per-purpose consent text | Rule 3 notice content and language needs. English and Hindi only |
| Consent | Per purpose, enforced, revocable, versioned | Whether clinician-recorded consent is valid. Whether other lawful uses apply to care |
| Purpose limitation | Consent checks at each use | Formal mapping |
| Access | Patients see records and access history | Export of a whole record isn't built |
| Correction | Allergies and profile fields, reviewed values | No formal request workflow |
| Deletion | Transcript only | Whole-record erasure isn't built, and medical retention duties may conflict |
| Retention | No periods set | See `DATA_PROTECTION_AND_RETENTION.md` |
| Processors | Groq, and Google when a Gemini key is set. Meko, hosted by Yugabyte, if patient packs are switched on | No signed agreement on file for any of them. Packs refuse to run with real patient data |
| Breach response | Audit data exists | No procedure. Secondary sources mention a 72-hour Board report under Rule 7 and a separate CERT-In duty. Our fetched reference gave no figures, so confirm from the Gazette |
| Grievance | Contact email only | No named contact or time limit. Confirm the Rule 14 period from the Gazette |
| Children | Newborn records, under-18 patients | Rule 10 parental consent, and any healthcare exemption. Not confirmed here |
| Significant data fiduciary | Not assessed | Counsel to assess |

Medical record retention duties, telemedicine rules and clinical establishment rules
were not researched in this review, and counsel should add them.

### C. AI vendor: Groq

**No real patient data is permitted today.** No signed agreement is on file, and a free plan key is
not an agreement. An API existing does not make it safe for patient data. Anthropic and Sarvam were
removed from the code on 31 October 2026.

Source: [Groq, Your Data](https://console.groq.com/docs/your-data), checked 31 October 2026.

| Question | Groq |
|---|---|
| Used for | Notes, prescription and lab reading, explanations, speech to text, English reading aloud |
| Data sent | Transcripts, audio, images of prescriptions and lab reports |
| Identifiable? | **Yes, likely.** Voices, names said aloud, names on images |
| Training | **Not stated on the page read.** Third-party sources say API data isn't used. Confirm in Groq's terms |
| Retention | "By default, Groq does not retain customer data for inference requests." Logs for troubleshooting or abuse are kept up to 30 days |
| Zero retention | "All customers may enable Zero Data Retention (ZDR) in Data Controls settings." **It must be switched on by the account owner.** This software can't do it or check it |
| Location | Retained data is in Google Cloud buckets in the United States, so this is a cross-border transfer |
| Model status | The vision model and the English voice are listed as preview: "should not be used in production environments" |
| Failure behaviour | Tested: reports failure, invents nothing |
| Protections to secure | Signed data agreement covering Indian law, training exclusion, retention, sub-processors, and a plan for preview models |

### C2. AI vendor: Google Gemini

**Also no real patient data.** A Gemini key is a second route for the same content: when Groq fails,
or when Settings make the providers take turns, a request goes to Google instead, with the same text
and images. Turning the second key on doubles the number of vendors that need an agreement.

| Question | Gemini |
|---|---|
| Used for | The same AI tools as Groq: notes, reading, explanations, interview questions. **Not speech.** It has no speech route here |
| Data sent | The same as for Groq, apart from audio |
| Training | **Not checked in this review.** The earlier notes in this repo say a free-tier key may let Google use content to improve its products. Confirm in Google's terms, and use a paid plan with training off before any real data |
| Location | Not checked |
| Model | `gemini-3.5-flash-lite` by default, chosen in Settings. Google changes and retires models often |
| Failure behaviour | Tested live: a deliberately wrong Groq key was answered by Gemini |

### C3. DiagnoFast speech additions (1 November 2026)

The kiosk can send question text to Microsoft through `edge-tts` (Neerja/Swara voices).
This is enabled by default and can be disabled with `DIAGNOSIA_NEURAL_TTS=0`.
Browser SpeechRecognition may also send patient audio to the browser's recognition provider.
In demo mode there is no consent screen, so nothing tells the patient about either path. One is needed before real use. Neither service has a reviewed data agreement or
retention assessment here; the existing **synthetic-data-only** rule applies. Patient confirmation
protects against transcription mistakes but does not replace vendor/privacy review.

### D. LOINC

The [licence page](https://loinc.org/kb/license) refused automated reading (HTTP 403), so
this comes from a search summary of it. **The exact notice was not read and is not
reproduced anywhere in this project.** As summarised: use is free for commercial and
non-commercial purposes, copies must carry the notice with a version or release date,
products incorporating LOINC must carry a specific notice in their licence or terms for
online resources, codes in electronic messages need no notice, fields may not be changed,
names may not imply endorsement, and some codes carry third-party terms.

**Action:** a person copies the notice from the licence page into `THIRD_PARTY_NOTICES.md`
and the terms, then records `loinc_notice`. Until then, LOINC-coded content shouldn't be
distributed.

### E. ABDM

Current exports are generic FHIR R4. **They are not ABDM-compliant.** Sources here are
secondary ([AMRIT guide](https://piramal-swasthya.gitbook.io/amrit/architecture/integrations/abdm-fhir-developer-intro/amrit-abdm-integration),
[NRCeS guide](https://www.nrces.in/ndhm/fhir/r4/)), so confirm with the official sandbox documents.

Missing for a real integration: ABHA creation and verification (M1), facility and
professional registry IDs, HIP record sharing on consent (M2), HIU record requests (M3),
gateway callbacks, consent artefacts from the ABDM consent manager, NRCeS bundle profiles
validated in CI, SNOMED and other required terminology, sandbox credentials,
NHA-recognised functional testing, and a CERT-In-empanelled security audit. The sources
disagree on milestone scope, including whether there is an M4.

## Reporting

Before any pilot with real people, name an owner for clinical incidents and define how a
suspected software error is reported, investigated and told to affected patients.
