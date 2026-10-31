# FHIR mapping

Exports are generic FHIR R4, built in `backend/records/fhir.py`. Internal tables are
not FHIR resources. No LOINC mapping has been approved yet, so exports hold no LOINC codes today.

**These exports are not ABDM-compliant.** They have not been validated against the
NRCeS profiles or with the HL7 validator, and no terminology beyond LOINC codes is
applied. Don't describe them as compliant until both are done.

| Our record | FHIR resource | Export |
|---|---|---|
| `patients` | Patient | `GET /api/patients/{id}/fhir` |
| `encounters` | Encounter | Same bundle |
| `observations` (accepted or corrected only) | Observation | Same bundle |
| `medication_records` (reconciled and accepted only) | MedicationStatement | Same bundle |
| `allergies` | AllergyIntolerance | Same bundle |
| `tasks` | Task | Same bundle |
| `referrals` | ServiceRequest | Same bundle |
| `referral_appointments` | Appointment, `basedOn` the ServiceRequest | Same bundle |
| `clinical_notes` (signed only) | Composition | Same bundle, or `GET /api/addons/clinscribe/notes/{id}/fhir` |
| `lab_reports` (confirmed only) | DiagnosticReport with Observations | `GET /api/addons/lablens/reports/{id}/fhir` |

Anything pending review is left out of every export. A LOINC code is exported only once
its mapping is approved. Until then, an observation carries the laboratory's own name as text.

## Not mapped yet

`Consent`, `AuditEvent`, `Provenance`, `CarePlan`, `Practitioner`, `Organization`,
`DocumentReference` and `Specimen` have matching data but no exporter.

## Next steps for India

1. Confirm the current NRCeS release. The blueprint saw v6.5.0 and a v7.0.0 preview.
2. Choose which bundles are needed, such as OP consultation, diagnostic report and
   discharge summary.
3. Run the HL7 validator with the NRCeS package in CI.
4. Add SNOMED CT coding where the profiles require it, after checking licensing.
