"""Editable project input; each accepted edit creates an immutable Brief."""
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.research import Label, ResearchBrief, SourcePolicy, RunBudget

TEMPLATES = {
    'technical': ('核心概念与机制', '方法比较与适用边界', '实现与验证路径'),
    'industry': ('产业链与主要参与者', '技术路线与应用', '数据口径与风险'),
}


class ProjectInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    question: str = Field(max_length=4000)
    template: Literal['technical', 'industry']
    dimensions: tuple[Label, ...] | None = Field(default=None, min_length=1, max_length=6)
    as_of: date | None = None
    region: Label | None = None
    allow_network: bool = Field(default=False, strict=True)
    output_requirements: Label = '约1500字，包含来源、比较、适用边界和证据缺口'
    expected_revision: int = Field(default=0, strict=True, ge=0)
    budget: RunBudget = Field(default_factory=RunBudget)

    def clarifications(self):
        issues = []
        if len(self.question.strip()) < 6 or self.question.strip() in {'研究一下这个领域', '帮我研究一下', '分析一下这个'}:
            issues.append('请明确研究对象和需要回答的问题，例如比较哪些方法或分析哪个行业。')
        if self.as_of is None:
            issues.append('请明确研究截止日期。')
        if self.template == 'industry' and self.region is None:
            issues.append('行业研究请明确地域范围，例如中国、全球。')
        return issues

    def brief(self, project_id, revision):
        if self.clarifications():
            raise ValueError('Research input requires clarification')
        return ResearchBrief(project_id=project_id, project_revision=revision,
                             question=self.question, template=self.template,
                             dimensions=self.dimensions or TEMPLATES[self.template], as_of=self.as_of,
                             region=self.region, output_requirements=self.output_requirements,
                             source_policy=SourcePolicy(allow_network=self.allow_network), budget=self.budget)
