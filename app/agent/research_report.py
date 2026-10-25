"""Deterministic, source-linked results including explicitly unreviewed progress."""
from uuid import UUID
from urllib.parse import quote

from app.domain.findings import Finding


REASONS = {
    'candidate_coverage_ready': '各研究维度已有候选证据，提前转入复核',
    'time_reserve': '接近研究时限，预留时间整理成果',
    'token_reserve': '接近Token目标，预留用量整理成果',
    'external_reserve': '接近外部请求额度，预留调用整理成果',
    'tool_reserve': '接近工具额度，预留调用整理成果',
    'source_read_reserve': '已用完正文读取额度，转入成果整理',
    'search_reserve': '已用完检索额度，转入成果整理',
    'external_call_limit': '达到外部请求次数上限',
    'tool_call_limit': '达到工具调用次数上限',
    'search_limit': '达到检索上限', 'source_read_limit': '达到正文读取上限',
    'token_target_admission': '剩余Token目标不足以准入下一次请求',
    'wall_time_limit': '达到研究时限',
    'repeated_tool_without_progress': '重复调用未带来新进展',
    'agent_finished': '研究Agent主动结束', 'execution_failed': '执行异常停止',
    'provider_timeout': '模型服务请求超时', 'graph_step_limit': '达到任务图步数上限',
}


def plain(value):
    """Do not let model/source text introduce Markdown links or headings."""
    return str(value).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('\\', '\\\\').replace('[', '\\[').replace(']', '\\]').replace('#', '\\#').replace('*', '\\*').replace('`', '\\`').replace('_', '\\_').replace(':', '&#58;').replace('\n', ' ')


def original_link(source):
    """Only persisted web origins become links; encode Markdown delimiters."""
    if source.spec.kind != 'web':
        return ''
    url = quote(source.spec.origin, safe=':/?&=%#@+;,$!~*-._')
    return f'[原网页](<{url}>)'


def unreviewed_findings(run, findings):
    # Legacy finalizations reviewed all findings available at that time.
    reviewed = set(run.get('reviewed_finding_ids',
                           [str(item.finding_id) for item in findings] if 'selected_finding_ids' in run else []))
    return [item for item in findings if str(item.finding_id) not in reviewed]


def render_preview(store, brief, run, findings):
    selected = set(run.get('selected_finding_ids', []))
    kept = [item for item in findings if str(item.finding_id) in selected]
    if not kept:
        return None
    lines = ['## ' + ('初步概览' if run.get('mode') == 'overview' else '本轮回答'), '']
    for item in kept[:6]:
        lines.extend(['- ' + plain(item.content.claim), ''])
    sources = {}
    for item in kept:
        for eid in item.evidence_ids:
            evidence = store.get_evidence(brief.project_id, eid)
            source = store.get_source(brief.project_id, evidence.source_version_id)
            sources[str(source.source_version_id)] = source
    lines.extend(['来源：' + '；'.join(f'[{plain(source.spec.title[:100])}](/api/research/projects/{brief.project_id}/sources/{sid})' + (' · ' + original_link(source) if original_link(source) else '') for sid, source in sources.items()), '',
                  '边界：' + '；'.join(dict.fromkeys(plain(item.content.uncertainty) for item in kept[:3])),
                  '以上为模型整理，原文定位可回查，尚未独立人工复核。'])
    if run.get('mode') == 'overview':
        lines.append('本轮仅是基于已读来源的初步概览，不代表全面的行业或领域调查。')
    return '\n'.join(lines)


def render_report(store, brief, run, findings):
    scope = run.get('scope')
    if scope:
        brief = brief.model_copy(update={'question': scope['question'], 'dimensions': tuple(scope['dimensions'])})
    selected = set(run.get('selected_finding_ids', []))
    drafts = unreviewed_findings(run, findings)
    reason = run.get('stop_reason') or (run.get('budget_state') or {}).get('stopped_reason') or 'agent_finished'
    lines = ['# ' + plain(brief.question), '',
             f'研究截止：{brief.as_of}；地域：{plain(brief.region or "未限定")}；项目版本：{brief.project_revision}',
             f'Run：{run["run_id"]}；路径：{run["route"]}', '',
             f'## {"研究报告" if selected else "阶段研究记录"}', '',
             '结束/收尾原因：' + plain(REASONS.get(reason, reason)), '',
             '> 以下内容由模型提出；原文和定位已校验，语义尚未独立人工复核。未复核草稿单独列出，不作为报告结论。来源获取时间不等于发布日期，日期未知的资料仅作背景。', '']
    if scope:
        lines.extend(['研究阶段：' + plain(scope['label']), ''])
        if run.get('parent_run_id'):
            lines.extend(['基于已有成果继续；父Run：' + run['parent_run_id'], ''])
        if selected:
            lines.extend(['## ' + ('初步概览' if scope['mode'] == 'overview' else '本轮回答'), ''])
            lines.extend('- ' + plain(item.content.claim) for item in findings if str(item.finding_id) in selected)

    def append_finding(item):
        finding = item.content
        lines.extend([f'- {plain(finding.claim)}（{finding.assessment}；{finding.claim_type}）',
                      f'  - 边界/不确定性：{plain(finding.uncertainty)}'])
        if finding.claim_type == 'gap':
            lines.append('  - 缺口仅针对本次已读证据；不能据此证明全文或全网没有该信息。')
        if finding.metric:
            details = [f'数值口径：{plain(finding.metric)} = {plain(finding.value)} {plain(finding.unit)}', '类型：' + finding.numeric_kind]
            for key, label in [('reporting_period', '统计期'), ('dataset', '数据集'), ('model_setup', '模型/配置'), ('region', '地域')]:
                if getattr(finding, key):
                    details.append(label + '：' + plain(getattr(finding, key)))
            lines.append('  - ' + '；'.join(details))
        for eid in item.evidence_ids:
            evidence = store.get_evidence(brief.project_id, eid)
            source = store.get_source(brief.project_id, evidence.source_version_id)
            url = f'/api/research/projects/{brief.project_id}/sources/{source.source_version_id}'
            locator = f'PDF第{evidence.locator.page}页' if evidence.locator.page else f'第{evidence.locator.paragraph}段'
            lines.extend([f'  - [{plain(source.spec.title)} · {locator}]({url})：{plain(evidence.quote)}',
                          f'    - 来源版本：{source.source_version_id}；获取：{source.spec.retrieved_at.isoformat()}；发布：{source.spec.published_at or "未知"}'])
            if original_link(source):
                lines.append('    - ' + original_link(source))

    for dimension in brief.dimensions:
        lines.extend(['## ' + plain(dimension), ''])
        matching = [item for item in findings if item.content.dimension == dimension and str(item.finding_id) in selected]
        if not matching:
            lines.extend(['本维度尚无经主Agent筛选的报告结论，证据不足或未完成复核。', ''])
        for item in matching:
            append_finding(item)

    if drafts:
        lines.extend(['', '## 待主Agent复核的研究草稿', '',
                      '> 下列仅通过引用位置校验，尚未经过主Agent语义筛选，不是已确认结论。', ''])
        for item in drafts:
            lines.append('### ' + plain(item.content.dimension))
            append_finding(item)
    if not findings:
        lines.extend(['', '尚未形成带引文的研究草稿，本次没有可交付的研究结论。已选资料不代表已经阅读。'])

    lines.extend(['', '## 资料与后续步骤', ''])
    read_ids = set(run.get('read_source_version_ids', []))
    for sid in run['source_version_ids']:
        source = store.get_source(brief.project_id, UUID(sid))
        url = f'/api/research/projects/{brief.project_id}/sources/{sid}'
        state = '已返回正文摘录' if sid in read_ids else ('尚未返回正文摘录' if 'read_source_version_ids' in run else '本次无已读记录（历史运行可能未采集）')
        lines.append(f'- [{plain(source.spec.title)}]({url})：{state}' + (' · ' + original_link(source) if original_link(source) else ''))
    if not run['source_version_ids']:
        lines.append('- 尚无已保存正文来源；检索摘要不可作为正文证据。')
    if drafts:
        lines.append('- 先对照原文复核上面的草稿，再将可靠结论纳入报告。')
    missing = [dimension for dimension in brief.dimensions if not any(item.content.dimension == dimension and str(item.finding_id) in selected for item in findings)]
    if missing:
        lines.append('- 待补充/复核维度：' + '、'.join(plain(item) for item in missing))
    if run.get('error'):
        lines.append('- 运行诊断：' + plain(run['error']))
    if run.get('finalization_error'):
        lines.append('- 收尾诊断：' + plain(run['finalization_error']))
    lines.extend(['', '## 冲突、缺口与复核', '',
                  'supports/partial/conflicts为模型支持关系判断；不同统计期、单位、范围的数值不可直接合并。未获取正文的候选与失败记录见运行详情。',
                  plain(run.get('review_notes') or '主Agent尚未完成结论筛选；当前记录用于保留进度。')])
    if scope and selected:
        lines.extend(['', '## 可选择的深入方向', '', '本轮已停止，不会自动继续。可在工作台选择方向启动一个新的有界任务。', ''])
        lines.extend('- ' + plain(value) for value in run.get('follow_up_options', []))
    delta = run.get('incremental_delta')
    if delta:
        lines.extend(['', '## 与上轮相比', '', '基线：' + plain(delta['baseline_run_id']), plain(delta['warning'])])
        for key, title in [('added', '本次新增表述'), ('changed', '已确认替代关系'), ('unchanged', '表述与引用版本完全相同'), ('needs_review', '旧证据待复核'), ('not_revisited', '本轮未形成完全相同记录')]:
            lines.extend(['', '### ' + title, ''])
            lines.extend('- ' + plain(value) for value in delta[key])
            if not delta[key]: lines.append('- 无记录。')
    return '\n'.join(lines) + '\n'


def restore_stopped_report(store, run):
    """Backfill legacy controlled stops locally, never call a provider or un-reject findings."""
    if run.get('job_id') or run.get('report') or run['status'] != 'partial':
        return
    reason = (run.get('budget_state') or {}).get('stopped_reason')
    if not reason:
        error = run.get('error', '')
        if error.startswith(('APITimeoutError:', 'TimeoutError:')):
            reason = 'provider_timeout'
        elif error.startswith('GraphRecursionError:'):
            reason = 'graph_step_limit'
    if reason not in REASONS or reason in ('agent_finished', 'execution_failed'):
        return
    brief = store.get_brief(UUID(run['project_id']), run['project_revision'])
    findings = [Finding.model_validate(value) for value in run.get('findings', [])]
    run['stop_reason'] = reason
    run['report_kind'] = 'progress'
    run['report'] = render_report(store, brief, run, findings)
    try:
        store.update_run(run, publishing=True)
    except ValueError:  # Stale/cancelled publication remains forbidden.
        run['report'] = None
        run['report_unavailable_reason'] = '项目版本或运行状态已改变，本次报告不能发布；已保存草稿仍可在运行详情回查。'
