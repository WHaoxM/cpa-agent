"""Portrait domain schemas: quoted observations are not confirmed facts."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AssessmentObject(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class AssessmentQuote(AssessmentObject):
    evidence_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    page: int | None
    block_id: str = Field(min_length=1)
    quote: str = Field(min_length=1)
    quote_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SchoolTypeObservation(AssessmentObject):
    value: str = Field(min_length=1)
    evidence: list[AssessmentQuote] = Field(min_length=1)


class QualityAssessment(AssessmentObject):
    level: Literal["强", "中", "弱"] | None
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=1)
    evidence: list[AssessmentQuote]
    situation: AssessmentQuote | None
    action: AssessmentQuote | None
    outcome: AssessmentQuote | None

    @model_validator(mode="after")
    def scored_items_need_evidence(self):
        if self.level is not None and not self.evidence:
            raise ValueError("assessment_level_requires_evidence")
        return self


class PortraitAssessmentProposal(AssessmentObject):
    school_type: SchoolTypeObservation | None
    communication: QualityAssessment
    teamwork: QualityAssessment
    stress_tolerance: QualityAssessment
    responsibility: QualityAssessment


class AssessmentReference(AssessmentObject):
    """Provider selects a server-owned ID; it cannot write quote/page/hash."""
    evidence_id: str = Field(min_length=1)


class SchoolTypeSelection(AssessmentObject):
    value: str = Field(min_length=1)
    evidence: list[AssessmentReference] = Field(min_length=1)


class QualitySelection(AssessmentObject):
    level: Literal["强", "中", "弱"] | None
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=1)
    evidence: list[AssessmentReference]
    situation: AssessmentReference | None
    action: AssessmentReference | None
    outcome: AssessmentReference | None


class PortraitAssessmentSelection(AssessmentObject):
    school_type: SchoolTypeSelection | None
    communication: QualitySelection
    teamwork: QualitySelection
    stress_tolerance: QualitySelection
    responsibility: QualitySelection


class AssessmentProvenance(AssessmentObject):
    kind: Literal["model_assessment"]
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    rubric_version: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sources_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class PortraitAssessmentCandidate(AssessmentObject):
    status: Literal["candidate"]
    assessment: PortraitAssessmentProposal
    provenance: AssessmentProvenance
    candidate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
