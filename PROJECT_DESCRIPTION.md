# Diagnosia

> **AI tools that give India's doctors back their time, and give patients understanding and independence.**

Diagnosia is a healthcare platform made of four AI-powered tools. Two of them
help **doctors** work faster. Two of them help **patients** understand their
care and stay independent. All four start from the same problem: India has too
few doctors for the number of people who need them.

| Tool | Who uses it | What it does in one line |
|---|---|---|
| **Skin Cancer Detection** | Doctors | Screens a photo of a skin lesion and gives the doctor an early, objective second opinion before costly tests. |
| **DiagnoFast** | Doctors (patients fill it in) | Takes the patient's history by voice in the waiting room and hands the doctor a ready-made report. |
| **OptiKinesis** | Patients who cannot use their hands | Lets a person control a computer with head movements and blinks, using only a normal webcam. |
| **Prescription Analyzer** | Patients | Reads a doctor's prescription and explains it in plain language, with diet, exercise and follow-up guidance. |

---

## 1. The problem

### Too few doctors, too many patients

India's doctors are badly overstretched, and the gap is widest in rural areas.
In many government hospitals and rural clinics, one doctor serves several
thousand people. The result is the same everywhere:

- **Long queues.** Patients wait for hours, often after travelling a long way.
- **Very short consultations.** A doctor may have only a few minutes for each
  patient.
- **Repeated questions.** Much of those few minutes goes on the same basic
  questions: *What is the problem? Since when? Any fever? Any old illness? What
  medicines are you taking?* While this happens, everyone else in the waiting
  room sits idle.
- **Late diagnosis.** Serious conditions such as skin cancer are often found
  late, because screening depends on a specialist's time and on expensive
  tests such as biopsies and scans.

### Patients are left behind after the consultation too

- **Prescriptions are hard to understand.** Handwriting is often unreadable,
  and shorthand such as *1-0-1*, *BD*, *TDS*, *SOS* or *HS* means nothing to
  most patients. Many people do not know when to take a medicine, what to eat
  or avoid, or when to come back.
- **People who lose the use of their hands lose access to the digital world.**
  ALS, spinal cord injuries, stroke and cerebral palsy can leave a person's
  mind fully active while making a mouse and keyboard impossible to use.
  Specialist eye-tracking equipment costs lakhs of rupees and is out of reach
  for most Indian families.

### What Diagnosia does about it

Diagnosia does not try to replace doctors. It removes the repetitive and slow
parts of their work so each doctor can see more patients properly. It also
helps patients understand and act on the care they receive.

---

## 2. How the four tools fit together

The tools can be used on their own, but they connect naturally along a
patient's journey through a clinic:

```
  WAITING ROOM              DOCTOR'S CABIN               AFTER THE VISIT
┌──────────────────┐     ┌──────────────────────┐     ┌──────────────────────┐
│   DiagnoFast     │     │  Doctor reads the    │     │ Prescription Analyzer│
│                  │     │  DiagnoFast report   │     │                      │
│ Voice questions  │ ──▶ │                      │ ──▶ │ Explains medicines,  │
│ Voice answers    │     │  Skin Cancer         │     │ diet, exercise and   │
│ ABHA history     │     │  Detection, if a     │     │ the next appointment │
│ Ready report     │     │  skin lesion is seen │     │                      │
└──────────────────┘     └──────────────────────┘     └──────────────────────┘

                OptiKinesis: for patients who cannot use their hands,
                lets them use a computer independently, at home or in hospital.
```

- **Before the consultation**, DiagnoFast collects the patient's story and
  medical history, so the doctor starts already informed.
- **During the consultation**, Skin Cancer Detection gives the doctor a quick,
  consistent screening for any suspicious skin lesion.
- **After the consultation**, the Prescription Analyzer helps the patient
  follow the treatment correctly.
- **OptiKinesis** serves a specific group of patients: people with motor
  disabilities who need hands-free access to a computer.

---

## 3. Skin Cancer Detection

### A screening aid for doctors, not a yes/no answer for patients

Skin Cancer Detection is built for **doctors**. It does not tell a patient
"you have cancer" or "you are fine". It gives the doctor a fast, objective
second opinion, so that suspicious lesions are noticed early and the right
patients are sent for further tests first.

### Why it matters

- Skin cancer is far more treatable when it is caught early.
- Confirming it needs a biopsy, and sometimes imaging. These are expensive,
  slow and need resources that are scarce outside big cities.
- Judging a lesion by eye depends on the doctor's experience and can vary from
  one doctor, or one day, to the next. A trained model looks at every image the
  same way.

Diagnosia adds a quick preliminary screening step **before** those costly
tests. It helps the doctor decide which patients need a biopsy urgently and
which can be monitored.

### How it works

1. **Capture.** The doctor or an assistant takes a photo of the lesion with a
   phone camera or a dermatoscope and uploads it.
2. **Stage 1: Is it suspicious?** A first neural network decides whether the
   image looks like healthy skin or a possible lesion.
3. **Stage 2: What type could it be?** If it looks suspicious, a second,
   stronger network (based on EfficientNet-B0) estimates which of these it is
   most likely to be:

   | Code | Condition | Type |
   |---|---|---|
   | `mel` | Melanoma | Malignant (most dangerous) |
   | `bcc` | Basal cell carcinoma | Malignant |
   | `akiec` | Actinic keratoses / intraepithelial carcinoma (Bowen's disease) | Pre-cancerous or early cancer |
   | `bkl` | Benign keratosis-like lesions | Benign |
   | `df` | Dermatofibroma | Benign |
   | `nv` | Melanocytic nevi (moles) | Benign |
   | `vasc` | Vascular lesions | Usually benign |
   | `healthy` | Healthy skin | — |

4. **Report for the doctor.** The doctor sees:
   - the most likely conditions, ranked with confidence scores;
   - a **highlight map** showing which parts of the image drove the result,
     so the doctor can check the model's reasoning instead of trusting a bare
     number;
   - a suggested priority, for example *"refer for biopsy soon"* or *"monitor
     and review"*.
5. **The doctor decides.** The final call on biopsies, scans or treatment
   always stays with the doctor.

### Training data

The models are trained on **HAM10000** ("Human Against Machine with 10,000
training images"), a widely used public collection of dermatoscopic images of
skin lesions, published on the Harvard Dataverse. Healthy skin images were added
to it.

### Responsible use

- The tool supports a doctor's judgement; it never replaces it.
- HAM10000 mostly contains images of lighter skin. Before wide use in India,
  the models need to be retrained and tested on Indian skin tones.
- Accuracy will be measured against biopsy-confirmed cases before any clinical
  claims are made, and the tool will go through the regulatory review that
  medical software needs in India.

---

## 4. DiagnoFast

### The patient's history is ready before they walk in

DiagnoFast takes the patient's history **while they wait**, so the doctor
doesn't spend the consultation asking the same basic questions. When the
patient enters the cabin, the doctor already has a short, organised report.

DiagnoFast replaces Diagnosia's older Symptom Checker, which matched a list of
symptoms against a fixed table of diseases.

### Built around each doctor

Every doctor has a personal way of taking a history. A dermatologist asks
different questions from a paediatrician, and two general physicians may ask
the same things in a different order. So DiagnoFast does not use one generic
form. **The Diagnosia team designs a custom form for each doctor**, based on
how that doctor actually diagnoses.

### Made for rural India: voice, not typing

Many patients in rural India cannot comfortably read or fill in a written
form. DiagnoFast therefore works like a conversation:

1. **It asks out loud.** DiagnoFast speaks each question in the patient's own
   language, the way a doctor would, for example *"Kya bimari hai?"*
   ("What is the problem?").
2. **The patient answers out loud.** The patient explains the problem in their
   own words, as a voice note, for as long as they need.
3. **It transcribes the answer.** Speech recognition turns the voice note into
   text, including Hindi and other Indian languages.
4. **An AI agent fills in the form.** The agent reads the transcript and picks
   out the details the doctor's form needs, such as the main complaint, how
   long it has lasted, how severe it is, related symptoms, medicines being
   taken and allergies. If something important is missing or unclear, it asks
   a follow-up question.
5. **It adds past medical records from ABHA.** With the patient's consent,
   DiagnoFast fetches their history from **ABHA** (Ayushman Bharat Health
   Account, India's national digital health ID under the Ayushman Bharat
   Digital Mission). That can include past diagnoses, prescriptions and lab
   reports.
6. **It writes the report.** DiagnoFast combines everything into one short
   report written for the doctor.

The patient can do this on a tablet at the reception desk or on their own
phone while waiting.

### What the doctor receives

A one-page summary that can be read in under a minute:

- **Patient details:** name, age, sex, ABHA ID
- **Chief complaint:** the main problem, in a single line
- **History of the present illness:** what happened, since when, how it has
  changed
- **Answers to the doctor's own custom questions**
- **Past medical history from ABHA:** earlier conditions, medicines, recent
  reports
- **Current medicines and allergies**
- **Highlights:** anything that looks urgent or important, shown at the top
- **The patient's original voice note and transcript**, in case the doctor
  wants to hear it in the patient's own words

The doctor can still ask questions in the cabin to clarify anything. But they
start from a full picture instead of from nothing, so each consultation is
shorter and more thorough.

### Business model

| Plan | For | What's included |
|---|---|---|
| **Individual membership** | Single doctors and small clinics | DiagnoFast with the doctor's custom-made form, patient intake on tablet or phone, doctor dashboard |
| **Organisation plan** | Hospitals and multi-doctor organisations | DiagnoFast across many doctors and departments, each with their own forms, plus central management |

Designing and updating each doctor's custom form is done by the Diagnosia
development team as part of the plan.

---

## 5. OptiKinesis

### Hands-free computer control with an ordinary webcam

OptiKinesis lets people who cannot use their hands, such as people living with
ALS, spinal cord injury, stroke or cerebral palsy, use a computer on their own.
It needs only the webcam that is already built into a laptop. There is no
special hardware, sensor or implant.

### What it does

- **Head-pose cursor.** The computer's camera tracks 478 points on the face.
  Wherever the user points their head, the mouse cursor goes. The movement is
  smoothed so the cursor stays steady.
- **Personal 5-point calibration.** The user turns comfortably toward the
  centre of the screen and each corner. OptiKinesis fits the cursor to that
  person's own range of movement, even if they can turn further one way than
  the other.
- **Blink to click.** A deliberate blink clicks where the user is pointing.
  Very short (natural) and very long eye closures are ignored.
- **Smart on-screen keyboard.** It types into any app, suggests words so a
  whole word can be finished with one blink, and has a **Speak** button that
  turns speech into text.
- **Easy web access.** "Find answers" shows search results as large,
  blink-friendly cards. "Read this page" turns a cluttered web page into large,
  clean text without ads or pop-ups.
- **Voice commands, emergency SOS and caregiver controls.** The user can, for
  example, search by voice or send an alert to a family member. Caregivers can
  pause or re-centre the system with a single key.

### Why it matters

Commercial eye trackers and head-tracking systems cost from tens of thousands
to several lakh rupees, so most Indian families cannot afford them. OptiKinesis
brings the same independence on hardware people already own: typing a message
to family, reading the news, searching for information or calling for help,
without depending on someone else for every click.

### MotionGlass: an optional precision upgrade

**MotionGlass** is a planned lightweight wearable (glasses, a clip-on or a
headband) with a motion sensor that connects to OptiKinesis over Bluetooth. It
makes the cursor faster and more precise for users who need it. Users start
with the webcam-only version and add MotionGlass only if they need to; nothing
has to be replaced.

### Business model

| Tier | For | What's included |
|---|---|---|
| **OptiKinesis Access** (software, one-time purchase) | Individual users | Webcam cursor control, blink click, keyboard, voice, SOS |
| **MotionGlass Kit** (hardware add-on) | Users who need more precision | Motion-sensor wearable, Bluetooth setup, charger, setup guide |
| **OptiKinesis Care** (optional subscription) | Users and families who want ongoing help | Regular calibration check-ups, device health checks, priority support |
| **Institutional Bundle** | Hospitals, rehabilitation centres, NGOs | Multiple user profiles, staff training, managing devices across a fleet, support and replacements |

---

## 6. Prescription Analyzer

### A prescription the patient can actually understand

Doctors' prescriptions usually list only medicine names and doses, often in
handwriting that is hard to read and in shorthand that patients don't know.
The Prescription Analyzer turns a prescription into clear, practical guidance.

It replaces Diagnosia's older Prescription Generator, which showed fixed diet
and medicine information for a disease chosen from a list.

### How it works

1. **The patient takes a photo** of the prescription, handwritten or printed.
2. **AI reads it.** Handwriting recognition picks out medicine names, doses,
   timings, duration, the doctor's notes and any follow-up date.
3. **It decodes the shorthand.** Common prescription notation becomes plain
   language, for example:

   | On the prescription | What it means |
   |---|---|
   | `1-0-1` | One in the morning, none in the afternoon, one at night |
   | `OD` | Once a day |
   | `BD` | Twice a day |
   | `TDS` | Three times a day |
   | `HS` | At bedtime |
   | `SOS` | Only when needed |
   | `AC` / `PC` | Before food / after food |
   | `x 5 days` | Take it for five days |

4. **It explains it in the patient's language**, in text and by voice: what
   each medicine is for, when and how to take it, and for how long.

### Helpful guidance alongside the medicines

- **Foods to eat and foods to avoid**, based on the condition and the medicines
  (for example, foods that interfere with a particular medicine)
- **Exercises and yoga positions** suited to the condition
- **Lifestyle tips**, such as sleep, hydration and things to avoid
- **Next appointment reminder**, if the prescription mentions a follow-up date
- **Medicine schedule**, a simple daily timetable of what to take when

### Safety rules

- The analyzer **explains**; it never changes a dose or swaps a medicine.
- If any part of the prescription cannot be read with confidence, it says so
  clearly and asks the patient to confirm with the doctor or pharmacist,
  instead of guessing.
- Every explanation reminds the patient to follow their doctor's
  instructions.

---

## 7. Trust, privacy and safety

Diagnosia handles some of the most sensitive data a person has, so these
principles apply across every tool:

- **Doctors stay in charge.** Every clinical tool assists the doctor and never
  makes the final decision.
- **Consent first.** Patient records, including ABHA records, are only accessed
  with the patient's explicit consent, in line with India's Digital Personal
  Data Protection Act.
- **Minimum data.** Each tool collects only what it needs for its job.
- **Security.** Health data is encrypted when stored and when sent, and access
  is limited to the treating doctor and the patient.
- **Honesty about limits.** Each tool shows how confident it is and when it is
  unsure, and accuracy claims are made only after proper testing.

---

## 8. Who Diagnosia is for

| Customer | What they get |
|---|---|
| **Individual doctors and small clinics** | DiagnoFast membership and Skin Cancer Detection: more patients seen properly, in less time |
| **Hospitals and healthcare organisations** | DiagnoFast organisation plan, Skin Cancer Detection and the OptiKinesis Institutional Bundle |
| **Patients** | Prescription Analyzer for understanding their treatment, and DiagnoFast intake that works in their own language by voice |
| **People with motor disabilities, their families and NGOs** | OptiKinesis Access, MotionGlass and OptiKinesis Care |

---

## 9. Current status

Diagnosia began as a school project in 2023 with a set of basic health tools.
It is now being rebuilt around the four tools described above.

| Tool | Status |
|---|---|
| **Skin Cancer Detection** | Working prototype: both models are trained on HAM10000 and run in a web app. Being redesigned as a doctor-facing tool with proper highlight maps and validation. |
| **DiagnoFast** | In design. Replaces the old Symptom Checker. |
| **OptiKinesis** | Working Windows prototype: head-pose cursor, 5-point calibration, blink click, keyboard with word suggestions and voice typing, web reader and SOS. MotionGlass is planned. |
| **Prescription Analyzer** | In design. Replaces the old Prescription Generator. |

The earlier basic features (the Talkie chatbot, Curl Counter, BMI Calculator
and DocFinder doctor directory) have been removed to keep Diagnosia focused.

---

## 10. Technology

| Area | Technology |
|---|---|
| Skin lesion analysis | TensorFlow / Keras, EfficientNet-B0, OpenCV; trained on HAM10000 |
| Head and face tracking (OptiKinesis) | MediaPipe Face Landmarker, OpenCV, PyQt5 desktop overlay, PyAutoGUI |
| Voice questions and answers (DiagnoFast) | Text-to-speech and speech-to-text for Indian languages |
| Form filling and summaries (DiagnoFast) | An AI agent built on a large language model |
| Health records (DiagnoFast) | ABHA / Ayushman Bharat Digital Mission integration, with patient consent |
| Prescription reading | Handwriting recognition with a vision-capable AI model |
| Web platform | Web front end, with a back end for doctor accounts, forms and reports |

---

<div align="center">

**Diagnosia: faster diagnosis for doctors, clearer care for patients.**

</div>
