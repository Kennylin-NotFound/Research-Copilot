import { useEffect, useState } from 'react';
import { Button, Checkbox, Drawer, Form, Input, Modal, Select, Space, Tag, Upload } from 'antd';
import { UploadOutlined } from '@ant-design/icons';
import { MarkdownRenderer } from '../MarkdownRenderer';
import { SourceOrigin } from './SourceOrigin';
import { apiBase, json, request, researchBase, workspaceBase } from './api';
import type { Memory, Project, Run, Source } from './types';
import { defaultBudget, today } from './types';
import type { WorkspaceController } from './useWorkspace';

type SettingsValues = { title: string; question: string; template: 'technical' | 'industry'; as_of: string; region?: string; dimensions?: string; output_requirements?: string; allow_network?: boolean; memory_enabled?: boolean };
type SourceView = { source: Source; extracted: { units: { locator: { page?: number; paragraph?: number; section?: string }; text: string }[] } };
type Props = { w: WorkspaceController; panel: 'create' | 'settings' | 'sources' | 'memories' | null; close: () => void; sourceId: string | null; closeSource: () => void; viewSource: (id: string) => void; report: Run | null; closeReport: () => void };
const templateOptions = [{ value: 'technical', label: '技术 / 理论研究' }, { value: 'industry', label: '行业研究' }];

export function WorkspaceDialogs({ w, panel, close, sourceId, closeSource, viewSource, report, closeReport }: Props) {
  const [form] = Form.useForm<SettingsValues>();
  const template = Form.useWatch('template', form);
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState('');
  const [source, setSource] = useState<SourceView | null>(null);
  const [sourceQuery, setSourceQuery] = useState('');
  const [replacements, setReplacements] = useState<Record<string, string>>({});
  const [exclusion, setExclusion] = useState<string | null>(null);
  const [reason, setReason] = useState('');
  useEffect(() => {
    if (panel !== 'create' && panel !== 'settings') return;
    form.resetFields(); setFormError('');
    if (panel === 'create') form.setFieldsValue({ title: '', question: '', template: 'technical', as_of: today(), region: '全球', allow_network: true, memory_enabled: true });
    else if (w.project) form.setFieldsValue({ ...w.project.brief, title: w.project.title, region: w.project.brief.region || '', dimensions: w.project.brief.dimensions.join('\n'), allow_network: w.project.preferences.allow_network, memory_enabled: w.project.preferences.memory_enabled });
  }, [panel, w.pid]);
  useEffect(() => {
    if (panel === 'memories') void w.refreshMemories().catch(e => w.setError(String(e)));
  }, [panel]);
  useEffect(() => {
    const abort = new AbortController(); setSource(null); setSourceQuery('');
    if (!sourceId || !w.pid) return;
    request<SourceView>(researchBase(w.pid) + '/sources/' + sourceId, { signal: abort.signal }).then(setSource).catch(e => { if (!abort.signal.aborted) w.setError(String(e)); });
    return () => abort.abort();
  }, [sourceId, w.pid]);
  async function save(values: SettingsValues) {
    setSaving(true); setFormError('');
    try {
      const payload = { ...values, title: values.title.trim(), question: values.question.trim(), region: values.region?.trim() || null, dimensions: values.dimensions?.split('\n').map(value => value.trim()).filter(Boolean), budget: panel === 'settings' ? w.project!.preferences.budget : defaultBudget, allow_network: values.allow_network || false, memory_enabled: values.memory_enabled ?? true, expected_revision: panel === 'settings' ? w.project!.brief.project_revision : 0, expected_settings_revision: panel === 'settings' ? w.project!.settings_revision : 1 };
      const result = await request<Project>(workspaceBase + (panel === 'settings' ? `/${w.pid}/settings` : ''), json(panel === 'settings' ? 'PUT' : 'POST', payload));
      w.setProjects(previous => [result, ...previous.filter(item => item.project_id !== result.project_id)]);
      if (panel === 'create') w.setPid(result.project_id);
      else { w.setNetwork(result.preferences.allow_network); w.setMemoryEnabled(result.preferences.memory_enabled); }
      close();
    } catch (e) { setFormError(String(e)); } finally { setSaving(false); }
  }
  async function changeMemory(item: Memory, replacement?: string) {
    try { await request(researchBase(w.pid) + '/memories/' + item.memory_id + (replacement ? '/supersede' : ''), replacement ? json('POST', { replacement_id: replacement }) : { method: 'DELETE' }); await w.refreshMemories(); }
    catch (e) { w.setError(String(e)); }
  }
  return <>
    <Modal className="workspace-overlay" title={panel === 'create' ? '新建研究项目' : '项目设置'} open={panel === 'create' || panel === 'settings'} forceRender okText={panel === 'create' ? '创建项目' : '保存设置'} cancelText="取消" onCancel={close} onOk={() => form.submit()} confirmLoading={saving} width={580}>
      <Form form={form} layout="vertical" onFinish={save}>
        <Form.Item name="title" label="项目名称" rules={[{ required: true, whitespace: true, min: 2, max: 120, message: '请输入2—120个字符的项目名称' }]}><Input aria-label="项目名称" placeholder="例如：LoRA机制研究" /></Form.Item>
        <Form.Item name="question" label="研究目标" rules={[{ required: true, whitespace: true, min: 6, max: 4000, message: '请明确研究目标，至少6个字符' }]}><Input.TextArea aria-label="项目研究目标" rows={3} placeholder="希望围绕什么主题，解决哪些问题？" disabled={panel === 'settings' && w.projectBusy} /></Form.Item>
        <div className="settings-grid"><Form.Item name="template" label="研究类型"><Select aria-label="项目研究类型" options={templateOptions} disabled={panel === 'settings' && w.projectBusy} /></Form.Item><Form.Item name="as_of" label="截止日期" rules={[{ required: true }]}><Input type="date" aria-label="项目研究截止日期" disabled={panel === 'settings' && w.projectBusy} /></Form.Item></div>
        <Form.Item name="region" label={template === 'industry' ? '研究地域' : '研究地域（可选）'} rules={[{ required: template === 'industry', whitespace: true, message: '请填写研究地域' }]}><Input aria-label="项目研究地域" placeholder="例如：全球、中国" disabled={panel === 'settings' && w.projectBusy} /></Form.Item>
        {panel === 'settings' && <details className="advanced-project-settings"><summary>研究偏好</summary><Form.Item name="dimensions" label="研究维度"><Input.TextArea aria-label="项目研究维度" rows={3} disabled={w.projectBusy} /></Form.Item><Form.Item name="output_requirements" label="报告要求"><Input.TextArea aria-label="项目报告要求" rows={2} disabled={w.projectBusy} /></Form.Item></details>}
        <Space wrap><Form.Item name="allow_network" valuePropName="checked"><Checkbox>默认允许联网</Checkbox></Form.Item><Form.Item name="memory_enabled" valuePropName="checked"><Checkbox>共享项目记忆</Checkbox></Form.Item></Space>
        {formError && <p className="form-error" role="alert">{formError.replace(/^Error: /, '')}</p>}
      </Form>
    </Modal>
    <Drawer rootClassName="workspace-overlay" title="项目资料" open={panel === 'sources'} onClose={close} width="min(620px, 96vw)">
      {w.error && <p className="form-error" role="alert">{w.error.replace(/^Error: /, '')}</p>}
      <Upload beforeUpload={file => w.upload(file)} accept=".pdf,.txt,.md" showUploadList={false} multiple><Button type="primary" icon={<UploadOutlined />}>上传 PDF / TXT / Markdown</Button></Upload>
      <p className="workspace-muted">选择本轮可使用的资料（{w.sourceIds.length} / 12）</p>
      <div className="source-library">{w.sources.map(item => <div key={item.source_version_id} className="source-library-item"><Checkbox checked={w.sourceIds.includes(item.source_version_id)} disabled={w.sourceIds.length >= 12 && !w.sourceIds.includes(item.source_version_id)} onChange={event => w.setSourceIds(event.target.checked ? [...w.sourceIds, item.source_version_id] : w.sourceIds.filter(id => id !== item.source_version_id))}>{item.spec.title}</Checkbox><div><Tag>{item.spec.kind.toUpperCase()}</Tag><SourceOrigin source={item} compact /><Button type="link" size="small" onClick={() => viewSource(item.source_version_id)}>查看原文</Button><Button type="text" size="small" onClick={() => { setExclusion(item.source_version_id); setReason(''); }}>记录排除理由</Button></div></div>)}</div>
      {!w.sources.length && <p className="workspace-muted">尚未添加资料，也可以在对话输入区开启联网研究。</p>}
    </Drawer>
    <Drawer rootClassName="workspace-overlay" title="项目记忆" open={panel === 'memories'} onClose={close} width="min(650px, 96vw)">
      {w.error && <p className="form-error" role="alert">{w.error.replace(/^Error: /, '')}</p>}
      <div className="memory-library">{w.memories.map(item => <section className="memory-entry" key={item.memory_id}><div><Tag>{({ scope: '研究范围', reading: '阅读与排除', finding: '研究发现', question: '未解决问题' } as Record<string, string>)[item.kind]}</Tag><Tag color={item.state === 'stale' || item.state === 'conflict' ? 'orange' : undefined}>{({ active: '可复用', stale: '需要更新', conflict: '存在冲突', deleted: '已停用', superseded: '已替代' } as Record<string, string>)[item.state]}</Tag>{item.claim_type === 'inference' && <Tag>推断</Tag>}</div><p>{item.text}</p><Space wrap>{item.source_version_ids.map(id => <Button size="small" key={id} onClick={() => viewSource(id)}>查看依据</Button>)}{!['deleted', 'superseded'].includes(item.state) && <Button type="text" size="small" onClick={() => void changeMemory(item)}>停用</Button>}</Space>
        {!['deleted', 'superseded'].includes(item.state) && <details className="memory-replacement"><summary>用新记录替代</summary><Space><Select aria-label={'替代记录 ' + item.memory_id} style={{ width: 260 }} placeholder="选择同类记录" value={replacements[item.memory_id]} options={w.memories.filter(other => other.memory_id !== item.memory_id && other.kind === item.kind && ['active', 'conflict'].includes(other.state)).map(other => ({ label: other.text.slice(0, 45), value: other.memory_id }))} onChange={id => setReplacements(previous => ({ ...previous, [item.memory_id]: id }))} /><Button disabled={!replacements[item.memory_id]} onClick={() => void changeMemory(item, replacements[item.memory_id])}>替代</Button></Space></details>}
      </section>)}</div>{!w.memories.length && <p className="workspace-muted">研究形成带来源的结论后，会在这里保留可复用记录。</p>}
    </Drawer>
    <Drawer rootClassName="workspace-overlay" title={source?.source.spec.title || '查看原文'} open={!!sourceId} onClose={closeSource} width="min(900px, 96vw)">
      {source && <><Space><Tag>{source.source.spec.kind.toUpperCase()}</Tag><Button href={apiBase + researchBase(w.pid) + `/sources/${sourceId}/raw`}>下载原始文件</Button></Space><SourceOrigin source={source.source} /><Input className="source-query" aria-label="查找原文" placeholder="输入引文或关键词" value={sourceQuery} onChange={event => setSourceQuery(event.target.value)} allowClear />{source.extracted.units.filter(unit => sourceQuery ? unit.text.toLowerCase().includes(sourceQuery.toLowerCase()) : unit.text.length > 60 || source.extracted.units.length < 20).map((unit, i) => <section className="source-unit" key={i}><Tag>{unit.locator.page ? `第 ${unit.locator.page} 页` : `第 ${unit.locator.paragraph} 段`}</Tag>{unit.locator.section}<p>{unit.text}</p></section>)}</>}
    </Drawer>
    <Drawer rootClassName="workspace-overlay" title="研究报告" open={!!report} onClose={closeReport} width="min(940px, 96vw)">{report?.report && <MarkdownRenderer content={report.report.replaceAll('](/api/', `](${apiBase}/api/`)} onSourceLink={href => viewSource(new URL(href, apiBase).pathname.split('/').at(-1)!)} />}</Drawer>
    <Modal className="workspace-overlay" title="排除资料" open={!!exclusion} okText="保存排除理由" cancelText="取消" okButtonProps={{ disabled: reason.trim().length < 6 }} onCancel={() => setExclusion(null)} onOk={async () => { try { await request(researchBase(w.pid) + '/memory-exclusions', json('POST', { source_version_id: exclusion, reason: reason.trim() })); await w.refreshMemories(); setExclusion(null); } catch (e) { w.setError(String(e)); } }}><Input.TextArea aria-label="资料排除理由" value={reason} onChange={event => setReason(event.target.value)} rows={3} placeholder="为什么后续研究不使用这份资料？" /></Modal>
  </>;
}
