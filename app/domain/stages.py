"""Explicit user-visible research stages; immutable project Brief stays intact."""
from typing import Literal
import re

ResearchMode = Literal['quick', 'overview', 'focused', 'full']
PROFILES = {
    'quick': {'label': '快速解答', 'dimension': '主问题解答', 'target_notes': 1, 'source_limit': 2,
              'max_searches': 2, 'max_source_reads': 4, 'max_external_calls': 12, 'max_tool_calls': 18, 'max_wall_seconds': 120, 'target_total_tokens': 90000, 'max_parallel_subtasks': 1},
    'overview': {'label': '主题概览', 'dimension': '领域概览', 'target_notes': 3, 'source_limit': 3,
                 'max_searches': 2, 'max_source_reads': 6, 'max_external_calls': 16, 'max_tool_calls': 24, 'max_wall_seconds': 150, 'target_total_tokens': 120000, 'max_parallel_subtasks': 1},
    'focused': {'label': '定向深入', 'dimension': '选定方向深入', 'target_notes': 2, 'source_limit': 4},
}


def stage_scope(brief, mode: ResearchMode, focus=None, parent_run_id=None):
    if mode == 'full':
        return None
    if mode == 'focused' and (not parent_run_id or not focus or not focus.strip()):
        raise ValueError('定向深入需要选择一次已有成果并填写具体方向')
    if mode != 'focused' and (parent_run_id or focus):
        raise ValueError('只有定向深入可指定父任务与方向')
    profile = PROFILES[mode]
    scope = {'mode': mode, 'label': profile['label'], 'question': focus.strip() if focus else brief.question,
            'original_question': brief.question, 'dimensions': [profile['dimension']],
            'target_notes': profile['target_notes'], 'source_limit': profile['source_limit']}
    return question_scope(scope, scope['question'])


def question_scope(scope, question):
    """Honor explicit overview subquestions without an extra semantic planner.

    Only ordered circled outlines are parsed; free prose retains the bounded
    stage default. Coverage labels are not a proof of semantic completeness.
    """
    profile = PROFILES[scope['mode']]
    result = {**scope, 'question': question, 'dimensions': [profile['dimension']], 'target_notes': profile['target_notes']}
    result.pop('goal_contract', None)
    if scope['mode'] != 'overview':
        return result
    markers = re.findall(r'[①②③④⑤⑥]', question)
    if not 2 <= len(markers) <= 6 or markers != list('①②③④⑤⑥')[:len(markers)]:
        return result
    parts = re.split(r'[①②③④⑤⑥]', question)[1:]
    labels = [re.split(r'[；;。\n]', part, maxsplit=1)[0].strip(' \t:：、,.') for part in parts]
    if len(set(labels)) != len(labels) or any(not 2 <= len(label) <= 80 for label in labels):
        return result
    result.update(dimensions=labels, target_notes=max(profile['target_notes'], len(labels)), goal_contract='explicit_circled_topics')
    return result


def bounded_stage_budget(budget, mode):
    profile = PROFILES.get(mode, {})
    return budget.model_copy(update={key: min(getattr(budget, key), value) for key, value in profile.items() if key.startswith('max_') or key == 'target_total_tokens'})


def default_follow_ups(brief):
    return ([f'围绕“{brief.question[:120]}”解释核心推导与关键假设', f'围绕“{brief.question[:120]}”比较方法与适用条件', f'围绕“{brief.question[:120]}”给出实现和验证路径']
            if brief.template == 'technical' else [f'围绕“{brief.question[:120]}”分析产业链与主要参与者', f'围绕“{brief.question[:120]}”比较技术路线与应用', f'围绕“{brief.question[:120]}”核查数据口径与风险'])
