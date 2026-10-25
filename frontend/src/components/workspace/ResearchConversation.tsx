import { useEffect, useRef } from 'react';
import { Alert, Button, Checkbox, Input, Select, Tag, Tooltip } from 'antd';
import { ArrowUpOutlined, FileTextOutlined, PaperClipOutlined, MenuOutlined, ProfileOutlined, StopOutlined } from '@ant-design/icons';
import { MarkdownRenderer } from '../MarkdownRenderer';
import { apiBase, researchBase } from './api';
import { activeStatus } from './types';
import type { Message, Run } from './types';
import type { WorkspaceController } from './useWorkspace';

export function cleanAnswer(value: string) {
  return value.split('\n').map(line => line.replace(/沿用历史(inference|gap)标记，未因复用升级为事实。/g, '').replace('该结论为模型判断，引用定位验证不代表语义已获人工确认。', '').replace(/^边界：；/, '边界：')).filter(line => !/^以上为模型整理，原文定位可回查，尚未独立人工复核。$/.test(line) && !/^边界：\s*$/.test(line) && !/^## (本轮回答|初步概览)$/.test(line)).join('\n');
}
export function ResearchConversation({ w, openProjects, openInspector, openSources, openReport, openSource }: { w: WorkspaceController; openProjects: () => void; openInspector: () => void; openSources: () => void; openReport: (run: Run) => void; openSource: (id: string) => void }) {
  const scroll = useRef<HTMLDivElement>(null);
  const input = useRef<null | { focus: () => void }>(null);
  const tail = w.messages.at(-1);
  const loaded = useRef('');
  useEffect(() => { const element = scroll.current; if (element && (loaded.current !== w.cid || element.scrollHeight - element.scrollTop - element.clientHeight < 260 || tail?.role === 'user')) { element.scrollTop = element.scrollHeight; loaded.current = w.cid; } }, [w.cid, tail?.message_id, tail?.status, tail?.content]);
  useEffect(() => { if (scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight; }, [w.cid]);
  const parent = w.messages.find(item => item.message_id === w.replyTo);
  const answerExists = w.messages.some(item => item.run?.report && !!item.run.selected_finding_ids?.length);
  const active = w.messages.find(item => activeStatus(item.status));
  const disabled = !w.project || !w.conversation || !!w.conversation.archived || w.projectBusy || w.sending;
  function chooseFollowUp(message: Message, value: string) { w.prefill(value, message.message_id); w.setRunId(message.run_id); input.current?.focus(); }
  return <section className="workspace-chat" aria-label="研究对话">
    <header className="conversation-topbar"><div className="conversation-title"><Button className="mobile-project-button" type="text" aria-label="打开项目导航" icon={<MenuOutlined />} onClick={openProjects} /><div><small>{w.project?.title || 'Research Copilot'}</small><h1>{w.conversation?.title || '开始你的研究'}</h1></div></div><div className="conversation-actions">{w.conversation?.kind === 'independent' && <Button type="text" size="small" onClick={() => void w.editConversation(w.cid, { archived: !w.conversation?.archived })}>{w.conversation.archived ? '恢复对话' : '归档'}</Button>}<Button type="text" icon={<ProfileOutlined />} onClick={openInspector}>执行记录</Button></div></header>
    <div className="conversation-scroll" ref={scroll}>
      {!w.messages.length && <div className="workspace-welcome"><span className="welcome-symbol">✳</span><h2>{w.project ? '从一个问题开始' : '把你的研究放进一个项目'}</h2><p>{w.project ? w.project.brief.question : '新建项目，整理资料，逐步探索你关心的问题。'}</p>{w.project && <div className="welcome-prompts"><Button onClick={() => w.setText(w.project!.brief.question)}>先研究这个问题</Button><Button onClick={openSources} icon={<PaperClipOutlined />}>添加研究资料</Button></div>}</div>}
      <div className="conversation-messages" aria-live="polite">{w.messages.map(message => message.role === 'user' ? <article key={message.message_id} className="workspace-message user-message"><div className="message-meta">你</div><div className="user-content">{message.content}</div></article> : <article key={message.message_id} className={'workspace-message assistant-message ' + (w.runId === message.run_id ? 'selected-message' : '')} onClick={() => w.setRunId(message.run_id)}>
        <div className="message-meta"><span className="agent-avatar">R</span><strong>Research Copilot</strong>{message.kind === 'legacy' && <Tag>历史研究</Tag>}{message.run?.claim_types?.includes('inference') && <Tag>含分析推断</Tag>}{message.run?.claim_types?.includes('gap') && <Tag color="orange">证据缺口</Tag>}</div>
        {message.kind === 'legacy' && <p className="legacy-question">{message.legacy_question}</p>}
        {message.content ? <MarkdownRenderer content={cleanAnswer(message.content).replaceAll('](/api/', `](${apiBase}/api/`)} onSourceLink={href => openSource(new URL(href, apiBase).pathname.split('/').at(-1)!)} /> : activeStatus(message.status) ? <div className="research-pending"><span className="thinking-dot" />{message.status === 'queued' ? '已收到问题，正在排队…' : '正在研究你的问题…'}</div> : <p className="workspace-muted">{message.status === 'cancelled' ? '本次研究已停止。' : message.run?.report ? '本次未形成可交付结论，已保留阶段记录与待补充项。' : message.run?.report_unavailable_reason ? '研究范围或资料已更新，请基于当前设置重新核查。' : '这次没有形成可交付的结论，可以调整问题后再试。'}</p>}
        {message.status === 'partial' && <div className="partial-notice">{message.run?.selected_finding_ids?.length ? '本次已结束，保留当前结论与待补充项。' : '可查看阶段记录，调整问题或额度后再试。'}</div>}
        <div className="answer-actions">{message.run?.report && <><Button type="text" size="small" icon={<FileTextOutlined />} onClick={() => openReport(message.run!)}>查看{message.run.report_kind === 'progress' ? '阶段记录' : '完整报告'}</Button><Button type="text" size="small" href={apiBase + researchBase(w.pid) + `/runs/${message.run_id}/report`}>下载</Button></>}<Button type="text" size="small" onClick={() => { w.setRunId(message.run_id); openInspector(); }}>执行记录</Button>{message.run?.report && !!message.run.selected_finding_ids?.length && <Button type="text" size="small" onClick={() => chooseFollowUp(message, '')}>继续追问</Button>}</div>
        {!activeStatus(message.status) && !!message.run?.selected_finding_ids?.length && <div className="followup-chips">{message.run.follow_up_options?.slice(0, 3).map(value => <button key={value} disabled={w.sending || !!w.conversation?.archived} onClick={() => chooseFollowUp(message, value)}>{value}</button>)}</div>}
      </article>)}</div>
    </div>
    <div className="workspace-composer-wrap">
      {w.error && <Alert className="workspace-input-error" showIcon type="error" message={w.error.replace(/^Error: /, '')} closable onClose={() => w.setError('')} />}
      {parent && <div className="reply-context"><span>继续：{parent.run?.scope?.question || parent.legacy_question || '所选回答'}</span><Button type="text" size="small" aria-label="取消指定追问" onClick={() => w.setReplyTo(null)}>×</Button></div>}
      {w.projectBusy && !active && <div className="composer-status">项目中还有研究任务，可以先查看其他对话。</div>}
      {active && <div className="composer-status"><span className="thinking-dot" />研究进行中<Button type="text" size="small" icon={<StopOutlined />} onClick={() => void w.cancel(active.run_id)}>停止</Button></div>}
      <div className="workspace-composer"><Input.TextArea ref={ref => { input.current = ref; }} aria-label="研究问题或追问" placeholder={w.project ? '输入问题，或继续之前的研究…' : '请先新建或选择项目'} autoSize={{ minRows: 2, maxRows: 7 }} value={w.text} disabled={!w.project || !!w.conversation?.archived || w.sending} maxLength={4000} onChange={event => w.setText(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void w.send(); } }} />
        <div className="composer-tools"><div className="composer-options"><Tooltip title="选择资料或上传附件"><Button type="text" size="small" aria-label="选择研究资料" icon={<PaperClipOutlined />} onClick={openSources} disabled={!w.project} /></Tooltip><Select aria-label="研究方式" size="small" value={w.mode} onChange={w.setMode} variant="borderless" options={[{ value: 'auto', label: '自动' }, { value: 'quick', label: '快速解答' }, { value: 'overview', label: '主题概览' }, { value: 'focused', label: '定向深入', disabled: !answerExists }]} /><Checkbox checked={w.network} onChange={event => w.setNetwork(event.target.checked)}>联网</Checkbox><Checkbox checked={w.memoryEnabled} onChange={event => { w.setMemoryEnabled(event.target.checked); if (!event.target.checked) w.setIncremental(false); }}>项目记忆</Checkbox><Checkbox checked={w.incremental} disabled={!w.memoryEnabled || !answerExists} onChange={event => w.setIncremental(event.target.checked)}>增量对照</Checkbox></div><Button type="primary" aria-label="发送研究问题" icon={<ArrowUpOutlined />} disabled={disabled || !w.text.trim()} loading={w.sending} onClick={() => void w.send()} />
        </div>
      </div>
      <div className="composer-shortcut">Enter 发送 · Shift + Enter 换行</div>
    </div>
  </section>;
}
