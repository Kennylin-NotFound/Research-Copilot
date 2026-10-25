import { Button, Empty, Tag } from 'antd';
import { CloseOutlined } from '@ant-design/icons';
import { BudgetCard } from './BudgetCard';
import { activeStatus, statusLabel, stopLabel } from './types';
import type { Event, Mode, Run } from './types';
import type { WorkspaceController } from './useWorkspace';

const toolLabel: Record<string, string> = { list_research_sources: '查看项目资料', search_research_sources: '检索公开资料', acquire_research_source: '获取来源正文', read_research_source: '阅读原文', record_research_note: '整理研究笔记', record_research_finding: '整理证据', review_research_findings: '复核证据', finalize_research_findings: '整理研究结论', task: '委派研究子任务', write_todos: '规划研究步骤' };
function elapsed(run: Run) { if (!run.created_at) return '—'; return Math.max(0, Math.round(((run.finished_at ? Date.parse(run.finished_at) : Date.now()) - Date.parse(run.created_at)) / 1000)) + ' 秒'; }
export function ExecutionInspector({ w, close, nextMode }: { w: WorkspaceController; close: () => void; nextMode: Mode }) {
  const run = w.run;
  const events = run?.events || [];
  const tasks = events.filter(item => item.event === 'tool_start');
  const recentActive = [...w.messages].reverse().find(item => activeStatus(item.status));
  const historical = !!recentActive && recentActive.run_id !== run?.run_id;
  function completion(start: Event) { return events.find(item => item.invocation_id === start.invocation_id && ['tool_end', 'tool_error'].includes(item.event)); }
  return <div className="execution-inspector">
    <header className="inspector-title"><div><small>研究过程</small><h2>执行面板</h2></div><Button type="text" aria-label="收起执行面板" icon={<CloseOutlined />} onClick={close} /></header>
    {historical && <div className="history-notice">正在查看历史任务 <Button size="small" type="link" onClick={() => w.setRunId(recentActive!.run_id)}>返回当前任务</Button></div>}
    <section className="execution-card"><div className="card-heading"><h3>本次任务</h3>{run && <Tag color={run.status === 'completed' ? 'green' : activeStatus(run.status) ? 'blue' : 'orange'}>{run.phase === 'finalizing' && activeStatus(run.status) ? '整理回答中' : statusLabel[run.status] || run.status}</Tag>}</div>
      {run ? <><p className="task-question">{run.scope?.question || '历史研究'}</p><div className="metric-row"><span>耗时</span><span>{elapsed(run)}</span></div>{run.stop_reason && <p className="workspace-muted">{stopLabel[run.stop_reason] || run.stop_reason}</p>}{activeStatus(run.status) && <Button size="small" danger onClick={() => void w.cancel(run.run_id)}>停止本次研究</Button>}</> : <p className="workspace-muted">发送问题，或选择一条回答查看记录。</p>}
    </section>
    <BudgetCard budget={w.budget} setBudget={w.setBudget} run={run} mode={nextMode} profiles={w.profiles} save={w.saveBudget} />
    <section className="execution-card"><div className="card-heading"><h3>调用记录</h3><span className="workspace-muted">{tasks.length} 步</span></div>
      {tasks.length ? <ol className="execution-timeline">{tasks.map(start => { const end = completion(start); const failed = end?.event === 'tool_error' || end?.status === 'error'; return <li key={start.seq} className={failed ? 'step-error' : end ? 'step-done' : 'step-active'}><details><summary><span className="step-dot">{failed ? '!' : end ? '✓' : '·'}</span><span>{toolLabel[start.tool || ''] || start.tool || '执行步骤'}</span><small>{end ? Math.max(0, (Date.parse(end.at) - Date.parse(start.at)) / 1000).toFixed(1) + 's' : activeStatus(run?.status) ? '进行中' : '已结束'}</small></summary><div className="step-details">{start.arguments && <><b>输入参数</b><pre>{start.arguments}</pre></>}{end?.result && <><b>返回摘要</b><pre>{end.result}</pre></>}{end?.error && <pre>{end.error}</pre>}<small>{new Date(start.at).toLocaleTimeString()}</small></div></details></li>; })}</ol> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无调用记录" />}
    </section>
    {run && <section className="execution-card"><details><summary>用量与诊断</summary><dl className="diagnostic-summary"><dt>任务编号</dt><dd>{run.run_id}</dd><dt>已复用项目记忆</dt><dd>{run.memory_used_ids?.length || 0} 条</dd>{run.project_usage && <><dt>项目累计请求</dt><dd>{run.project_usage.external_calls} 次</dd><dt>项目累计已知 Token</dt><dd>{run.project_usage.known_tokens.toLocaleString()}</dd><dt>项目模型用量未知</dt><dd>{run.project_usage.unknown_model_calls} 次</dd></>}{run.error && <><dt>错误信息</dt><dd>{run.error}</dd></>}</dl><details><summary>原始运行记录</summary><pre className="raw-events">{JSON.stringify({ events: run.events, usage: run.usage, budget: run.budget_state }, null, 2)}</pre></details></details></section>}
  </div>;
}
