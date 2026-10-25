import { useState } from 'react';
import { Button, Input, Modal, Tooltip } from 'antd';
import { FolderOpenOutlined, PlusOutlined, SettingOutlined, FileTextOutlined, BookOutlined, MessageOutlined, MoreOutlined, DeleteOutlined, UndoOutlined } from '@ant-design/icons';
import { request, workspaceBase } from './api';
import type { WorkspaceController } from './useWorkspace';

export function ProjectSidebar({ w, openCreate, openSettings, openSources, openMemories, afterNavigate }: { w: WorkspaceController; openCreate: () => void; openSettings: () => void; openSources: () => void; openMemories: () => void; afterNavigate?: () => void }) {
  const [query, setQuery] = useState('');
  const [archived, setArchived] = useState(false);
  const [name, setName] = useState('');
  const [nameOpen, setNameOpen] = useState(false);
  const [renameId, setRenameId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<{ project_id: string; title: string } | null>(null);
  const [trashOpen, setTrashOpen] = useState(false);
  const [trash, setTrash] = useState<{ project_id: string; title: string; deleted_at: string }[]>([]);
  const [restoring, setRestoring] = useState<string | null>(null);
  async function openTrash() {
    w.setError('');
    try { setTrash(await request<{ project_id: string; title: string; deleted_at: string }[]>(workspaceBase.replace('/projects', '/trash'))); setTrashOpen(true); }
    catch (e) { w.setError(String(e)); }
  }
  async function saveName() {
    if (!name.trim()) return;
    setSaving(true);
    try { if (renameId) await w.editConversation(renameId, { title: name.trim() }); else await w.addConversation(name.trim()); setNameOpen(false); }
    catch (e) { w.setError(String(e)); } finally { setSaving(false); }
  }
  return <div className="project-navigation">
    <div className="workspace-brand"><span className="workspace-logo">R</span><div>Research Copilot<small>研究工作台</small></div></div>
    <Button type="primary" block icon={<PlusOutlined />} onClick={openCreate}>新建项目</Button>
    <Input aria-label="搜索项目" placeholder="搜索项目" value={query} onChange={event => setQuery(event.target.value)} allowClear className="project-search" />
    <nav aria-label="项目列表" className="project-cards">
      {w.projects.filter(item => item.title.toLowerCase().includes(query.toLowerCase()) || item.brief.question.toLowerCase().includes(query.toLowerCase())).map(item => <button className={'project-card ' + (w.pid === item.project_id ? 'selected' : '')} key={item.project_id} aria-pressed={w.pid === item.project_id} onClick={() => { w.setPid(item.project_id); afterNavigate?.(); }}>
        <FolderOpenOutlined /><span><strong>{item.title}</strong><small>{item.active_run_id ? '研究进行中' : item.brief.template === 'industry' ? '行业研究' : '技术研究'}</small></span>{item.active_run_id && <span className="running-dot" />}
      </button>)}
    </nav>
    <Button type="text" size="small" icon={<DeleteOutlined />} onClick={() => void openTrash()}>项目回收站</Button>
    {w.project && <><div className="sidebar-section-heading"><span>项目对话</span><Tooltip title="新建独立对话"><Button aria-label="新建独立对话" size="small" type="text" icon={<PlusOutlined />} onClick={() => { setRenameId(null); setName(''); setNameOpen(true); }} /></Tooltip></div>
      <nav aria-label="对话列表" className="conversation-links">
        {w.conversations.filter(item => !!item.archived === archived).map(item => <div className={'conversation-link ' + (item.conversation_id === w.cid ? 'selected' : '')} key={item.conversation_id}>
          <button onClick={() => { w.chooseConversation(item.conversation_id); afterNavigate?.(); }} aria-current={item.conversation_id === w.cid ? 'page' : undefined}><MessageOutlined /><span>{item.title}</span></button>
          <Tooltip title="重命名对话"><Button type="text" size="small" aria-label={'重命名 ' + item.title} icon={<MoreOutlined />} onClick={() => { setRenameId(item.conversation_id); setName(item.title); setNameOpen(true); }} /></Tooltip>
        </div>)}
      </nav>
      <Button size="small" type="text" onClick={() => setArchived(!archived)}>{archived ? '返回当前对话' : '已归档对话'}</Button>
      <div className="project-management"><Button type="text" icon={<SettingOutlined />} onClick={openSettings}>项目设置</Button><Button type="text" icon={<FileTextOutlined />} onClick={openSources}>项目资料</Button><Button type="text" icon={<BookOutlined />} onClick={openMemories}>项目记忆</Button><Tooltip title={w.projectBusy ? '请先停止正在进行的研究' : '移入回收站，可恢复'}><Button type="text" danger disabled={w.projectBusy || w.sending} icon={<DeleteOutlined />} onClick={() => { w.setError(''); setDeleteTarget({ project_id: w.project!.project_id, title: w.project!.title }); }}>删除项目</Button></Tooltip></div>
    </>}
    <div className="navigation-footer"><span className="service-dot" /> 本机研究空间</div>
    <Modal title={renameId ? '重命名对话' : '新建独立对话'} open={nameOpen} okText={renameId ? '保存' : '创建对话'} cancelText="取消" confirmLoading={saving} okButtonProps={{ disabled: !name.trim() }} onCancel={() => setNameOpen(false)} onOk={() => void saveName()}><Input aria-label="对话名称" placeholder="例如：参数量推导" maxLength={120} value={name} onChange={event => setName(event.target.value)} onPressEnter={() => void saveName()} /></Modal>
    <Modal className="workspace-overlay" title="删除项目" open={!!deleteTarget} okText="移入回收站" cancelText="取消" confirmLoading={saving} okButtonProps={{ danger: true }} onCancel={() => setDeleteTarget(null)} onOk={async () => { if (!deleteTarget) return; setSaving(true); try { await w.deleteProject(deleteTarget.project_id); setDeleteTarget(null); } catch (e) { w.setError(String(e)); } finally { setSaving(false); } }}><p>将“{deleteTarget?.title}”移入回收站？</p><p className="workspace-muted">项目对话、资料和研究成果会一同隐藏，可在项目回收站恢复。</p>{w.error && <p className="form-error" role="alert">{w.error.replace(/^Error: /, '')}</p>}</Modal>
    <Modal className="workspace-overlay" title="项目回收站" open={trashOpen} footer={null} onCancel={() => setTrashOpen(false)} width={600} styles={{ body: { maxHeight: '65vh', overflowY: 'auto' } }}>{w.error && <p className="form-error" role="alert">{w.error.replace(/^Error: /, '')}</p>}{!trash.length ? <p className="workspace-muted">回收站为空</p> : trash.map(item => <div className="trash-project" key={item.project_id}><div><strong>{item.title}</strong><small>{new Date(item.deleted_at).toLocaleString()}</small></div><Button icon={<UndoOutlined />} loading={restoring === item.project_id} disabled={!!restoring} onClick={async () => { setRestoring(item.project_id); try { await w.restoreProject(item.project_id); setTrash(previous => previous.filter(value => value.project_id !== item.project_id)); setTrashOpen(false); afterNavigate?.(); } catch (e) { w.setError(String(e)); } finally { setRestoring(null); } }}>恢复项目</Button></div>)}</Modal>
  </div>;
}
