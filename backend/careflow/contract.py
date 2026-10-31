"""What a CareFlow event may contain. Anything not listed here is refused.

An event says what happened and in which module, never what was found. There are no free-text
fields, because free text is where a name or a result would slip in.
"""
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

SCHEMA_VERSION = "1.1"
ACCEPTED_VERSIONS = ("1.0", "1.1")  # 1.0 events may still wait in an outbox. See upgrade().
WORKFLOW_REF = re.compile(r"^case_[a-z0-9_]{3,40}$")
TENANT_REF = re.compile(r"^tenant_[a-z0-9_]{3,40}$")
TECHNICAL_ID = re.compile(r"^[a-z]+_[a-z0-9]{6,40}$")
MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{1,60}$")
OPERATION = re.compile(r"^[a-z_]+(\.[a-z_]+)+$")
SOURCE_REF = re.compile(r"^[a-f0-9]{16,40}$")

EVENT_TYPES: Dict[str, set] = {
    "DiagnoFast": {
        "intake.link_created", "intake.started", "intake.transcription.completed", "intake.completed",
        "intake.review_required", "intake.failed",
    },
    "Skin Cancer Detection": {"skin_analysis.started", "skin_analysis.completed", "skin_analysis.review_required", "skin_analysis.failed"},
    "Prescription Analyzer": {
        "prescription.processing.started", "prescription.extraction.completed", "prescription.confirmation_required",
        "prescription.explained", "prescription.processing.failed",
    },
    "ClinScribe": {
        "note_session.started", "note_session.recording", "note_session.paused", "note_session.stopped",
        "note_session.transcript_corrected", "note_session.transcript_deleted", "note_session.transcription_failed",
        "note_draft.created", "note_draft.updated", "note_draft.approved", "note_draft.discarded", "note_draft.failed",
    },
    "LabLens": {
        "lab.report_received", "lab.extraction.started", "lab.extraction.completed", "lab.review_required",
        "lab.extraction.failed", "lab.extraction.retried", "lab.result_reviewed", "lab.result_added", "lab.report_confirmed",
    },
    "DischargeCare": {"discharge.episode_created", "discharge.episode_updated", "discharge.checklist.updated", "discharge.approved", "discharge.completed"},
    "ReferralLoop": {
        "referral.created", "referral.sent", "referral.acknowledged", "referral.needs_information", "referral.accepted",
        "referral.declined", "referral.redirected", "referral.appointment_offered", "referral.scheduled", "referral.rescheduled",
        "referral.attended", "referral.no_show", "referral.priority_changed", "referral.contact_attempted",
        "referral.outcome_returned", "referral.reviewed", "referral.completed",
    },
}
MODULES = set(EVENT_TYPES)
# Tools that were removed. Their old events can still sit in an outbox or in MongoDB, so a database
# validator keeps accepting them. New events from these modules are refused.
RETIRED_MODULES = {"MedGuard", "ChronicCare", "MaternaCare", "AMR Guard"}

# The same order of a visit that the workspace uses.
WORKFLOW_STAGES = {
    "DiagnoFast": "before_visit",
    "ClinScribe": "during_visit",
    "LabLens": "during_visit",
    "Skin Cancer Detection": "during_visit",
    "Prescription Analyzer": "after_visit",
    "ReferralLoop": "after_visit",
    "DischargeCare": "after_visit",
}

# Each allowed metadata key and how its value is checked. Unknown keys are refused, not dropped,
# so a mistake shows up in tests instead of quietly leaking or vanishing.
METADATA: Dict[str, Any] = {
    "review_required": bool,
    "urgent_stop": bool,
    "item_count": int,
    "alert_count": int,
    "question_count": int,
    "duration_ms": int,
    "model_provider": {"groq", "gemini", "local", "demo_stub"},
    "model_name": MODEL_NAME,
    "extraction_method": {"pdf-text", "ai-vision", "manual", "none"},
    "language": {"en-IN", "hi-IN"},
    "error_category": {"ai_unavailable", "ai_rate_limited", "ai_declined", "ai_not_configured", "speech_unavailable", "speech_rejected", "model_not_installed", "processing_error"},
}
MAX_METADATA_KEYS = 8

Status = Literal["started", "completed", "review_required", "failed"]
ActorType = Literal["clinician", "patient", "system"]


class ContractError(ValueError):
    """This event isn't allowed, so it was not recorded."""


def check_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    if len(metadata) > MAX_METADATA_KEYS:
        raise ContractError("too many metadata fields")
    for key, value in metadata.items():
        rule = METADATA.get(key)
        if rule is None:
            raise ContractError(f"metadata field not allowed: {key}")
        if rule is bool:
            valid = isinstance(value, bool)
        elif rule is int:
            valid = isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 10_000_000
        elif isinstance(rule, set):
            valid = value in rule
        else:
            valid = isinstance(value, str) and rule.match(value) is not None
        if not valid:
            raise ContractError(f"metadata value not allowed for {key}")
    return metadata


class CareEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    event_type: str
    module: str
    workflow_stage: str
    source_system: Literal["diagnosia"] = "diagnosia"
    source_operation: str
    source_ref: Optional[str] = None
    workflow_ref: str
    tenant_ref: str
    correlation_id: str
    causation_id: Optional[str] = None
    occurred_at: datetime
    status: Status
    actor_type: ActorType
    target_module: Optional[str] = None
    metadata: Dict[str, Any] = {}
    data_classification: Literal["synthetic_demo"] = "synthetic_demo"

    @field_validator("workflow_ref")
    @classmethod
    def workflow_is_pseudonymous(cls, value: str) -> str:
        if not WORKFLOW_REF.match(value):
            raise ValueError("workflow_ref must be a generated reference")
        return value

    @field_validator("tenant_ref")
    @classmethod
    def tenant_is_pseudonymous(cls, value: str) -> str:
        if not TENANT_REF.match(value):
            raise ValueError("tenant_ref must be a generated reference")
        return value

    @field_validator("event_id", "correlation_id")
    @classmethod
    def identifier_is_generated(cls, value: str) -> str:
        if not TECHNICAL_ID.match(value):
            raise ValueError("identifiers must be generated")
        return value

    @field_validator("source_operation")
    @classmethod
    def operation_is_a_name(cls, value: str) -> str:
        if not OPERATION.match(value) or len(value) > 80:
            raise ValueError("source_operation must be an operation name")
        return value

    @field_validator("source_ref")
    @classmethod
    def source_ref_is_technical(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not SOURCE_REF.match(value):
            raise ValueError("source_ref must be a request ID")
        return value

    @field_validator("metadata")
    @classmethod
    def metadata_is_allowed(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        return check_metadata(value)

    @model_validator(mode="after")
    def event_belongs_to_module(self) -> "CareEvent":
        if self.module not in MODULES or self.event_type not in EVENT_TYPES[self.module]:
            raise ValueError("this module can't record this event")
        if self.target_module is not None and self.target_module not in MODULES:
            raise ValueError("unknown target module")
        if self.target_module == self.module:
            raise ValueError("a handoff needs a different target module")
        if self.workflow_stage != WORKFLOW_STAGES[self.module]:
            raise ValueError("this module belongs to a different stage")
        return self


def upgrade(event: dict) -> dict:
    """Brings an event written by an earlier version up to the current one, without changing its meaning."""
    if event.get("schema_version") == SCHEMA_VERSION:
        return event
    return {
        **event,
        "schema_version": SCHEMA_VERSION,
        "workflow_stage": WORKFLOW_STAGES.get(event["module"], "programme"),
        "source_system": "diagnosia",
        "source_operation": event["event_type"],
        "source_ref": None,
    }


@dataclass(frozen=True)
class Mapping:
    module: str
    event_type: str
    status: str = "completed"
    review_required: bool = False
    failed: Optional[str] = None  # recorded if the same request then fails
    target_module: Optional[str] = None
    deferred: bool = False  # the route records it after the AI has run, so it can name the model


def _same(module: str, event_type: str, actions: str) -> Dict[str, Mapping]:
    return {action: Mapping(module, event_type) for action in actions.split()}


# Audit actions that mark a real step. The audit row and the outbox row share a transaction, and a
# route that fails rolls both back, so a success event can't outlive the failure it describes.
AUDIT_EVENTS: Dict[str, Mapping] = {
    "diagnofast.link.created": Mapping("DiagnoFast", "intake.link_created"),
    "clinscribe.session.start": Mapping("ClinScribe", "note_session.started"),
    "clinscribe.session.recording": Mapping("ClinScribe", "note_session.recording"),
    "clinscribe.session.paused": Mapping("ClinScribe", "note_session.paused"),
    "clinscribe.session.stopped": Mapping("ClinScribe", "note_session.stopped"),
    "clinscribe.segment.correct": Mapping("ClinScribe", "note_session.transcript_corrected"),
    "clinscribe.transcript.delete": Mapping("ClinScribe", "note_session.transcript_deleted"),
    # Each chunk of a consultation is a step too small to show, but one that fails matters.
    "clinscribe.transcribe": Mapping("ClinScribe", "note_session.transcription_failed", failed="note_session.transcription_failed", deferred=True),
    "clinscribe.note.draft": Mapping("ClinScribe", "note_draft.created", "review_required", True, failed="note_draft.failed", deferred=True),
    "clinscribe.note.edit": Mapping("ClinScribe", "note_draft.updated"),
    "clinscribe.note.signed": Mapping("ClinScribe", "note_draft.approved"),
    "clinscribe.note.discard": Mapping("ClinScribe", "note_draft.discarded"),
    "lablens.report.upload": Mapping("LabLens", "lab.report_received", "started"),
    "lablens.report.retry": Mapping("LabLens", "lab.extraction.retried", "started"),
    "lablens.result.review": Mapping("LabLens", "lab.result_reviewed"),
    "lablens.result.add": Mapping("LabLens", "lab.result_added"),
    "lablens.report.confirmed": Mapping("LabLens", "lab.report_confirmed"),
    "dischargecare.episode.create": Mapping("DischargeCare", "discharge.episode_created"),
    "dischargecare.episode.update": Mapping("DischargeCare", "discharge.episode_updated"),
    "dischargecare.episode.approved": Mapping("DischargeCare", "discharge.approved"),
    "dischargecare.episode.close": Mapping("DischargeCare", "discharge.completed"),
    **_same(
        "DischargeCare", "discharge.checklist.updated",
        "dischargecare.instruction.add dischargecare.barrier.add dischargecare.barrier.resolve "
        "dischargecare.pending.add dischargecare.pending.finalise dischargecare.task.add",
    ),
    "referralloop.create": Mapping("ReferralLoop", "referral.created"),
    "referralloop.status.sent": Mapping("ReferralLoop", "referral.sent"),
    "referralloop.status.needs_information": Mapping("ReferralLoop", "referral.needs_information", "review_required", True),
    "referralloop.status.redirected": Mapping("ReferralLoop", "referral.redirected"),
    "referralloop.status.appointment_offered": Mapping("ReferralLoop", "referral.appointment_offered"),
    "referralloop.status.rescheduled": Mapping("ReferralLoop", "referral.rescheduled"),
    "referralloop.priority": Mapping("ReferralLoop", "referral.priority_changed"),
    "referralloop.contact_attempt": Mapping("ReferralLoop", "referral.contact_attempted"),
    "referralloop.status.acknowledged": Mapping("ReferralLoop", "referral.acknowledged"),
    "referralloop.status.accepted": Mapping("ReferralLoop", "referral.accepted"),
    "referralloop.status.declined": Mapping("ReferralLoop", "referral.declined"),
    "referralloop.status.booked": Mapping("ReferralLoop", "referral.scheduled"),
    "referralloop.status.attended": Mapping("ReferralLoop", "referral.attended"),
    "referralloop.status.no_show": Mapping("ReferralLoop", "referral.no_show"),
    "referralloop.outcome": Mapping("ReferralLoop", "referral.outcome_returned", "review_required", True),
    "referralloop.review": Mapping("ReferralLoop", "referral.reviewed"),
    "referralloop.close": Mapping("ReferralLoop", "referral.completed"),
}


# Write routes that deliberately record no event, and why. A test fails if a new write route is
# neither mapped nor listed here, so nothing can be missed quietly.
UNTRACKED_ROUTES: Dict[str, str] = {
    "add_typed_segment": "A line of transcript. Too small a step to show, and its content can't be recorded",
    "transcribe_chunk": "Runs every 25 seconds while recording. Only a failure is recorded",
    "read_aloud": "Reads text aloud and stores nothing",
    "approve": "LOINC mapping governance, kept in the audit log",
    "reject": "LOINC mapping governance, kept in the audit log",
    "correct": "LOINC mapping governance, kept in the audit log",
}
