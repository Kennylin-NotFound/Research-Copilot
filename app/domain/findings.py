"""Model assertions are distinct from deterministic quotation verification."""
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from app.domain.research import Contract, EvidenceLocator, Label


class CitationInput(Contract):
    source_version_id: UUID
    locator: EvidenceLocator
    quote: str = Field(min_length=1, max_length=4000)


class FindingContent(Contract):
    dimension: Label
    claim: Label
    assessment: Literal['supports', 'partial', 'conflicts'] = 'partial'
    claim_type: Literal['fact', 'inference', 'gap'] = 'inference'
    uncertainty: Label = '该结论为模型判断，引用定位验证不代表语义已获人工确认。'
    metric: Label | None = None
    value: Label | None = None
    unit: Label | None = None
    reporting_period: Label | None = None
    numeric_kind: Literal['unspecified', 'mathematical', 'benchmark', 'industry'] = 'unspecified'
    dataset: Label | None = None
    model_setup: Label | None = None
    region: Label | None = None

    @model_validator(mode='after')
    def metric_context(self):
        present = any((self.metric, self.value, self.unit, self.reporting_period, self.dataset, self.model_setup))
        if present:
            if not all((self.metric, self.value, self.unit)):
                raise ValueError('Numeric comparison requires metric, value and unit')
            if self.numeric_kind == 'unspecified' and not self.reporting_period:
                raise ValueError('Numeric comparison requires metric, value, unit and reporting period, or an explicit numeric_kind')
            if self.numeric_kind == 'benchmark' and not all((self.dataset, self.model_setup)):
                raise ValueError('Benchmark comparison requires dataset and model_setup; explicitly mark unknown rather than inventing them')
            if self.numeric_kind == 'industry' and not all((self.reporting_period, self.region)):
                raise ValueError('Industry comparison requires reporting_period and region')
        return self


class FindingInput(FindingContent):
    citations: tuple[CitationInput, ...] = Field(min_length=1, max_length=4)


class ResearchNoteInput(FindingContent):
    excerpt_ids: tuple[str, ...] = Field(min_length=1, max_length=3)


class Finding(Contract):
    finding_id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    content: FindingInput
    evidence_ids: tuple[UUID, ...]
