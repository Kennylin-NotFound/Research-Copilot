import { useState } from 'react';
import { Button, InputNumber, Popover } from 'antd';
import type { Budget, Mode, Run } from './types';
const fields: [keyof Budget, string][] = [['max_external_calls', '外部请求'], ['max_wall_seconds', '研究时限（秒）'], ['target_total_tokens', 'Token 目标'], ['max_searches', '检索次数'], ['max_source_reads', '正文读取'], ['max_tool_calls', '工具次数'], ['max_parallel_subtasks', '并发数']];
export function BudgetCard({ budget, setBudget, run, mode, profiles, save }: { budget: Budget; setBudget: (b: Budget) => void; run: Run | null; mode: Mode; profiles: Record<string, Partial<Budget>>; save: () => Promise<boolean | void> }) {
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const nextMode = mode === 'auto' ? (run?.report ? 'focused' : 'quick') : mode;
  const profile = profiles[nextMode] || {};
  const next = Object.fromEntries(Object.entries(budget).map(([key, value]) => [key, Math.min(value, profile[key as keyof Budget] ?? value)])) as Budget;
  return <section className="execution-card budget-card" aria-label="执行额度">
    <div className="card-heading"><h3>执行额度</h3><Popover open={open} onOpenChange={setOpen} trigger="click" placement="bottomRight" title="设置下次额度" content={<div className="budget-editor">{fields.map(([key, label]) => <label key={key}><span>{label}</span><InputNumber aria-label={'下次' + label} value={budget[key]} min={['max_wall_seconds', 'target_total_tokens', 'max_parallel_subtasks'].includes(key) ? 1 : 0} max={key === 'max_parallel_subtasks' ? 3 : undefined} precision={0} onChange={value => { if (typeof value === 'number') setBudget({ ...budget, [key]: value }); }} /></label>)}<Button type="primary" block loading={saving} onClick={async () => { setSaving(true); try { if (await save()) setOpen(false); } finally { setSaving(false); } }}>保存下次额度</Button></div>}><Button type="text" size="small">设置下次额度</Button></Popover></div>
    {run?.budget_state ? <><div className="metric-row"><span>本次请求</span><strong>{run.budget_state.external_calls} / {run.budget_state.limits.max_external_calls}</strong></div><div className="metric-row"><span>已知 Token</span><strong>{run.budget_state.known_tokens.toLocaleString()}</strong></div><div className="metric-row"><span>模型用量未知</span><strong>{run.budget_state.unknown_model_calls} 次</strong></div><div className="metric-row"><span>本次时限</span><span>{run.budget_state.limits.max_wall_seconds} 秒</span></div></> : <p className="workspace-muted">发送问题后显示本次用量</p>}
    <div className="next-budget">下次预期：至多 {next.max_external_calls} 次请求 · {next.max_wall_seconds} 秒</div>
  </section>;
}
