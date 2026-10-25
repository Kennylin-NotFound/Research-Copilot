"""Immutable research inputs and source/evidence contracts (schema v1)."""
from datetime import date, datetime, timezone
import hashlib
import json
from typing import Annotated, Literal
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4, uuid5

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
Digest = Annotated[str, StringConstraints(pattern=r'^[a-f0-9]{64}$')]
SourceKind = Literal['web', 'pdf', 'text']
SOURCE_NAMESPACE = UUID('4395752b-dbae-4c68-9aec-cd4562d8a7e6')


def utc_now():
    return datetime.now(timezone.utc)


def canonical_url(value: str) -> str:
    """Conservative identity normalization, not network-access authorization."""
    parts = urlsplit(value.strip())
    if parts.scheme.lower() not in ('http', 'https') or not parts.hostname or parts.username is not None or parts.password is not None:
        raise ValueError('Use an HTTP(S) URL without credentials')
    host = parts.hostname.encode('idna').decode('ascii').lower()
    if ':' in host:
        host = '[' + host + ']'
    port = parts.port
    scheme = parts.scheme.lower()
    authority = host if port is None or (scheme, port) in (('http', 80), ('https', 443)) else f'{host}:{port}'
    # Preserve query order and path escaping: these can change server semantics.
    return urlunsplit((scheme, authority, parts.path or '/', parts.query, ''))


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, validate_default=True, revalidate_instances='always')


class RunBudget(Contract):
    """Immutable limits configuration; shared accounting lives in runtime.budget."""
    budget_id: UUID = Field(default_factory=uuid4)
    max_searches: int = Field(default=8, strict=True, ge=0)
    max_source_reads: int = Field(default=10, strict=True, ge=0)
    max_external_calls: int = Field(default=20, strict=True, ge=0)
    max_tool_calls: int = Field(default=30, strict=True, ge=0)
    max_wall_seconds: int = Field(default=300, strict=True, gt=0)
    target_total_tokens: int = Field(default=120000, strict=True, gt=0)
    max_parallel_subtasks: int = Field(default=3, strict=True, ge=1, le=3)


class SourcePolicy(Contract):
    allow_network: bool = Field(default=False, strict=True)
    allowed_kinds: tuple[SourceKind, ...] = ('web', 'pdf', 'text')

    @field_validator('allowed_kinds')
    @classmethod
    def valid_kinds(cls, value):
        if not value or len(value) != len(set(value)):
            raise ValueError('Choose distinct, nonempty source kinds')
        return value


class ResearchBrief(Contract):
    schema_version: Literal[1] = 1
    brief_id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    project_revision: int = Field(strict=True, ge=1)
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
    template: Literal['technical', 'industry']
    dimensions: tuple[Label, ...] = Field(min_length=1, max_length=6)
    as_of: date
    region: Label | None = None
    output_requirements: Label = '生成含可回查来源、适用边界与证据缺口的研究报告'
    source_policy: SourcePolicy = Field(default_factory=SourcePolicy)
    budget: RunBudget = Field(default_factory=RunBudget)
    created_at: AwareDatetime = Field(default_factory=utc_now)

    @field_validator('dimensions')
    @classmethod
    def unique_dimensions(cls, value):
        if len({item.casefold() for item in value}) != len(value):
            raise ValueError('Research dimensions must be distinct')
        return value

    @field_validator('created_at')
    @classmethod
    def utc_created(cls, value):
        return value.astimezone(timezone.utc)


class SourceSpec(Contract):
    """A fetched/uploaded snapshot, before the store assigns immutable identities."""
    project_id: UUID
    kind: SourceKind
    origin: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
    title: Label
    publisher: Label | None = None
    published_at: AwareDatetime | None = None
    retrieved_at: AwareDatetime = Field(default_factory=utc_now)
    reporting_period: Label | None = None
    body_state: Literal['full_text', 'search_snippet'] = 'full_text'
    sections: tuple[Label, ...] = Field(default=(), max_length=8)

    @model_validator(mode='after')
    def identity(self):
        if len(set(self.sections)) != len(self.sections) or (self.sections and self.kind != 'web'):
            raise ValueError('Section selection requires distinct web section names')
        if self.kind == 'web':
            origin = canonical_url(self.origin)
        else:
            if not self.origin.startswith('upload:'):
                raise ValueError('File identity must use upload:<UUID>, never a user path')
            origin = 'upload:' + str(UUID(self.origin.removeprefix('upload:')))
            if self.body_state != 'full_text':
                raise ValueError('Search snippets are web discovery material only')
        object.__setattr__(self, 'origin', origin)
        return self

    @field_validator('published_at', 'retrieved_at')
    @classmethod
    def utc_dates(cls, value):
        return value.astimezone(timezone.utc) if value is not None else None


class EvidenceLocator(Contract):
    kind: Literal['pdf_page', 'web_paragraph', 'text_paragraph']
    page: int | None = Field(default=None, strict=True, ge=1)
    paragraph: int | None = Field(default=None, strict=True, ge=1)
    section: Label | None = None
    anchor: Label | None = None
    table: Label | None = None

    @model_validator(mode='after')
    def required_position(self):
        if self.kind == 'pdf_page':
            if self.page is None:
                raise ValueError('PDF evidence requires a real page number')
        elif self.page is not None or self.paragraph is None:
            raise ValueError('Web/text evidence requires a paragraph, without a PDF page')
        return self


def source_identity(spec: SourceSpec):
    return uuid5(SOURCE_NAMESPACE, str(spec.project_id) + '\0' + spec.kind + '\0' + spec.origin)


def snapshot_digest(spec: SourceSpec, raw_sha256: str, text_sha256: str):
    metadata = spec.model_dump(mode='json', exclude={'retrieved_at'})
    if not spec.sections:
        metadata.pop('sections', None)  # Preserve pre-selector v1 snapshot hashes.
    payload = {'spec': metadata,
               'raw_sha256': raw_sha256, 'text_sha256': text_sha256}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


class TextUnit(Contract):
    locator: EvidenceLocator
    text: str = Field(min_length=1, max_length=300000)

    @field_validator('text')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('Extracted text cannot be blank')
        return value


class ExtractedDocument(Contract):
    schema_version: Literal[1] = 1
    extractor: Label
    units: tuple[TextUnit, ...] = Field(min_length=1)
    total_pages: int | None = Field(default=None, strict=True, ge=1, le=50)

    @model_validator(mode='after')
    def distinct_positions(self):
        positions = [item.locator.model_dump_json() for item in self.units]
        if len(set(positions)) != len(positions):
            raise ValueError('Extracted unit positions must be distinct')
        if sum(len(item.text) for item in self.units) > 300000:
            raise ValueError('Extracted text exceeds the local 300000 character limit')
        return self

    def validate_kind(self, kind: SourceKind):
        expected = {'web': 'web_paragraph', 'pdf': 'pdf_page', 'text': 'text_paragraph'}[kind]
        if any(unit.locator.kind != expected for unit in self.units):
            raise ValueError('Source kind and extracted locators differ')
        if kind == 'pdf':
            if self.total_pages is None or any(unit.locator.page > self.total_pages for unit in self.units):
                raise ValueError('PDF page is outside the extracted document')
        elif self.total_pages is not None:
            raise ValueError('Web/text documents cannot claim a PDF page count')


class SourceVersion(Contract):
    schema_version: Literal[1] = 1
    source_version_id: UUID
    source_id: UUID
    spec: SourceSpec
    raw_sha256: Digest
    text_sha256: Digest
    snapshot_sha256: Digest
    raw_size: int = Field(strict=True, ge=1, le=8 * 1024**2)
    unit_count: int = Field(strict=True, ge=1)
    extractor: Label

    @model_validator(mode='after')
    def consistent_identity(self):
        if self.source_id != source_identity(self.spec) or self.snapshot_sha256 != snapshot_digest(self.spec, self.raw_sha256, self.text_sha256):
            raise ValueError('Source identity or snapshot checksum does not match metadata')
        if self.source_version_id != uuid5(self.source_id, self.snapshot_sha256):
            raise ValueError('Source version identity does not match its snapshot')
        return self


class Evidence(Contract):
    schema_version: Literal[1] = 1
    evidence_id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    source_version_id: UUID
    quote: str = Field(min_length=1, max_length=20000)
    locator: EvidenceLocator
    support_status: Literal['unknown', 'supports', 'partial', 'conflicts'] = 'unknown'
    assessment_note: Label | None = None

    @field_validator('quote')
    @classmethod
    def nonblank_quote(cls, value):
        if not value.strip():
            raise ValueError('Evidence quote cannot be blank')
        return value
