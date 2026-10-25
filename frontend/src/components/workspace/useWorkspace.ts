import { useEffect, useRef, useState } from 'react';
import { request, json, workspaceBase, researchBase } from './api';
import { activeStatus, defaultBudget } from './types';
import type { Budget, Conversation, Memory, Message, Mode, Project, Run, Source } from './types';

export function useWorkspace() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [pid, setPid] = useState<string>(() => localStorage.getItem('workspace-project') || localStorage.getItem('research-project') || '');
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [cid, setCid] = useState('');
  const [messages, setMessages] = useState<Message[]>([]);
  const [sources, setSources] = useState<Source[]>([]);
  const [sourceIds, setSourceIds] = useState<string[]>([]);
  const [memories, setMemories] = useState<Memory[]>([]);
  const [runId, setRunId] = useState('');
  const [run, setRun] = useState<Run | null>(null);
  const [text, setText] = useState('');
  const [replyTo, setReplyTo] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>('auto');
  const [network, setNetwork] = useState(false);
  const [memoryEnabled, setMemoryEnabled] = useState(false);
  const [incremental, setIncremental] = useState(false);
  const [budget, setBudget] = useState<Budget>(defaultBudget);
  const [profiles, setProfiles] = useState<Record<string, Partial<Budget>>>({});
  const [error, setError] = useState('');
  const [sending, setSending] = useState(false);
  const [loading, setLoading] = useState(true);
  const selection = useRef({ pid, cid }); selection.current = { pid, cid };
  const submission = useRef<{ payload: string; id: string } | null>(null);
  const sourceSelectionEdited = useRef(false);
  const preferencesLoaded = useRef('');
  const drafts = useRef(new Map<string, { text: string; replyTo: string | null }>());
  const project = projects.find(item => item.project_id === pid) || null;
  const conversation = conversations.find(item => item.conversation_id === cid) || null;
  const projectBusy = !!project?.active_run_id || messages.some(item => activeStatus(item.status));

  async function refreshProjects() {
    const values = await request<Project[]>(workspaceBase);
    setProjects(values);
    setPid(previous => values.some(item => item.project_id === previous) ? previous : values[0]?.project_id || '');
    return values;
  }
  useEffect(() => {
    let alive = true;
    refreshProjects().catch(e => { if (alive) setError(String(e)); }).finally(() => { if (alive) setLoading(false); });
    request<Record<string, Partial<Budget>>>('/api/research/stages').then(setProfiles).catch(() => undefined);
    const timer = window.setInterval(() => { void refreshProjects().catch(() => undefined); }, 5000);
    return () => { alive = false; window.clearInterval(timer); };
  }, []);

  useEffect(() => {
    if (!project || preferencesLoaded.current === project.project_id) return;
    preferencesLoaded.current = project.project_id;
    setBudget(project.preferences.budget); setNetwork(project.preferences.allow_network); setMemoryEnabled(project.preferences.memory_enabled);
  }, [pid, project?.project_id]);

  useEffect(() => {
    let alive = true;
    setConversations([]); setCid(''); setMessages([]); setRun(null); setRunId(''); setSources([]); setMemories([]); setReplyTo(null); setText(''); setError('');
    if (!pid) return;
    localStorage.setItem('workspace-project', pid);
    const selected = projects.find(item => item.project_id === pid);
    if (selected) { setBudget(selected.preferences.budget); setNetwork(selected.preferences.allow_network); setMemoryEnabled(selected.preferences.memory_enabled); }
    sourceSelectionEdited.current = false;
    setIncremental(false); setMode('auto');
    request<Conversation[]>(workspaceBase + `/${pid}/conversations`).then(values => {
      if (!alive) return;
      setConversations(values);
      const saved = localStorage.getItem('workspace-conversation:' + pid);
      const legacyRun = localStorage.getItem('research-run:' + pid);
      setCid(values.find(item => item.conversation_id === saved)?.conversation_id || (legacyRun ? values.find(item => item.kind === 'legacy')?.conversation_id : null) || values.find(item => item.kind === 'main')?.conversation_id || '');
    }).catch(e => { if (alive) setError(String(e)); });
    request<Source[]>(researchBase(pid) + '/sources').then(values => { if (alive) { setSources(values); setSourceIds(values.map(item => item.source_version_id).slice(0, 12)); } }).catch(e => { if (alive) setError(String(e)); });
    request<Memory[]>(researchBase(pid) + '/memories').then(values => { if (alive) setMemories(values); }).catch(() => undefined);
    return () => { alive = false; };
  }, [pid]);

  useEffect(() => {
    if (!pid || cid) return;
    let alive = true;
    const timer = window.setInterval(() => {
      request<Conversation[]>(workspaceBase + `/${pid}/conversations`).then(values => {
        if (!alive || selection.current.pid !== pid || selection.current.cid) return;
        setConversations(values);
        setCid(values.find(item => item.kind === 'main')?.conversation_id || ''); setError('');
      }).catch(() => undefined);
    }, 2500);
    return () => { alive = false; window.clearInterval(timer); };
  }, [pid, cid]);
  useEffect(() => {
    if (!pid || !run || activeStatus(run.status)) return;
    let alive = true;
    request<Source[]>(researchBase(pid) + '/sources').then(values => {
      if (!alive) return; setSources(values);
      if (!sourceSelectionEdited.current) setSourceIds(values.map(item => item.source_version_id).slice(0, 12));
    }).catch(() => undefined);
    void refreshMemories().catch(() => undefined);
    return () => { alive = false; };
  }, [pid, run?.run_id, run?.status]);

  async function refreshMessages(projectId: string, conversationId: string) {
    const values = await request<Message[]>(workspaceBase + `/${projectId}/conversations/${conversationId}/messages`);
    if (selection.current.pid !== projectId || selection.current.cid !== conversationId) return;
    setMessages(values); setError(previous => previous === '连接暂时中断，正在重新获取任务状态。' ? '' : previous);
    if (submission.current && values.some(item => item.role === 'user' && item.request_id === submission.current!.id)) {
      const saved = values.find(item => item.role === 'user' && item.request_id === submission.current!.id)!;
      if (drafts.current.get(conversationId)?.text.trim() === saved.content.trim()) { setText(''); setReplyTo(null); drafts.current.delete(conversationId); }
      submission.current = null;
    }
    setRunId(previous => {
      if (values.some(item => item.run_id === previous)) return previous;
      const saved = localStorage.getItem('workspace-run:' + conversationId) || localStorage.getItem('research-run:' + projectId);
      return values.find(item => item.run_id === saved)?.run_id || values.at(-1)?.run_id || '';
    });
  }
  useEffect(() => {
    let alive = true;
    setMessages([]); setRun(null); setRunId(''); setError('');
    const draft = drafts.current.get(cid); setText(draft?.text || ''); setReplyTo(draft?.replyTo || null);
    if (!pid || !cid) return;
    localStorage.setItem('workspace-conversation:' + pid, cid);
    void refreshMessages(pid, cid).catch(e => { if (alive) setError(String(e)); });
    const timer = window.setInterval(() => { void refreshMessages(pid, cid).catch(() => { if (alive) setError('连接暂时中断，正在重新获取任务状态。'); }); }, 1800);
    return () => { alive = false; window.clearInterval(timer); };
  }, [pid, cid]);
  useEffect(() => {
    let alive = true;
    setRun(null);
    if (!pid || !runId) return;
    localStorage.setItem('workspace-run:' + cid, runId);
    const load = () => request<Run>(researchBase(pid) + '/runs/' + runId).then(value => { if (alive) setRun(value); }).catch(() => undefined);
    void load();
    const timer = window.setInterval(() => { void load(); }, 1800);
    return () => { alive = false; window.clearInterval(timer); };
  }, [pid, runId]);
  useEffect(() => {
    if (!runId || !pid || !activeStatus(run?.status)) return;
    let alive = true; let cursor = Math.max(0, ...(run?.events || []).map(event => event.seq));
    let socket: WebSocket | undefined; let retry: ReturnType<typeof setTimeout> | undefined;
    const url = new URL(requestUrl(pid, runId));
    function connect() {
      if (!alive) return;
      url.searchParams.set('last_seq', String(cursor)); socket = new WebSocket(url);
      socket.onmessage = event => {
        const page = JSON.parse(event.data) as { run_id: string; last_seq: number; events: NonNullable<Run['events']> };
        if (page.run_id !== runId) return;
        cursor = Math.max(cursor, page.last_seq);
        setRun(previous => { if (previous?.run_id !== runId) return previous; const merged = new Map((previous.events || []).map(item => [item.seq, item])); page.events.forEach(item => merged.set(item.seq, item)); return { ...previous, events: [...merged.values()].sort((a, b) => a.seq - b.seq) }; });
      };
      socket.onclose = () => { if (alive) retry = setTimeout(connect, 1500); };
    }
    connect(); return () => { alive = false; if (retry) clearTimeout(retry); socket?.close(); };
  }, [pid, runId, activeStatus(run?.status)]);

  function chooseConversation(id: string) { if (cid) drafts.current.set(cid, { text, replyTo }); setCid(id); setMode('auto'); setIncremental(false); }
  function editText(value: string) { setText(value); if (cid) drafts.current.set(cid, { text: value, replyTo }); }
  function prefill(value: string, messageId: string) { setText(value); setReplyTo(messageId); setMode('auto'); if (cid) drafts.current.set(cid, { text: value, replyTo: messageId }); }
  async function send() {
    if (!project || !cid || !text.trim() || sending || projectBusy || conversation?.archived) return;
    if (!sourceIds.length && !network) { setError('请先选择或上传研究资料，或开启联网研究。'); return; }
    const projectId = pid, conversationId = cid;
    const inputs = { content: text.trim(), reply_to_message_id: replyTo, mode, source_version_ids: sourceIds, allow_network: network, memory_enabled: memoryEnabled, incremental, budget };
    const payload = JSON.stringify({ pid, cid, ...inputs });
    if (submission.current?.payload !== payload) submission.current = { payload, id: crypto.randomUUID() };
    setSending(true); setError('');
    try {
      const result = await request<Run>(workspaceBase + `/${pid}/conversations/${cid}/messages`, json('POST', { ...inputs, request_id: submission.current.id }));
      submission.current = null; drafts.current.delete(conversationId);
      if (selection.current.pid === projectId && selection.current.cid === conversationId) { setText(''); setReplyTo(null); setRunId(result.run_id); await refreshMessages(projectId, conversationId); }
      await refreshProjects();
    } catch (e) { if (selection.current.pid === projectId && selection.current.cid === conversationId) setError(String(e)); }
    finally { setSending(false); }
  }
  async function cancel(id: string) {
    try { await request(researchBase(pid) + '/runs/' + id + '/cancel', { method: 'POST' }); await refreshMessages(pid, cid); await refreshProjects(); }
    catch (e) { setError(String(e)); }
  }
  async function addConversation(title: string) {
    if (!title.trim()) return;
    const value = await request<Conversation>(workspaceBase + `/${pid}/conversations`, json('POST', { title: title.trim() }));
    setConversations(previous => [...previous, value]); chooseConversation(value.conversation_id);
  }
  async function deleteProject(projectId: string) {
    await request(workspaceBase + `/${projectId}`, { method: 'DELETE' });
    if (projectId === selection.current.pid) {
      for (const item of conversations) drafts.current.delete(item.conversation_id);
      preferencesLoaded.current = '';
      localStorage.removeItem('workspace-project');
      localStorage.removeItem('workspace-conversation:' + projectId);
    }
    await refreshProjects();
  }
  async function restoreProject(projectId: string) {
    await request(workspaceBase + `/${projectId}/restore`, { method: 'POST' });
    await refreshProjects(); setPid(projectId);
  }
  async function editConversation(id: string, changes: { title?: string; archived?: boolean }) {
    try {
      const value = await request<Conversation>(workspaceBase + `/${pid}/conversations/${id}`, json('PATCH', changes));
      setConversations(previous => previous.map(item => item.conversation_id === id ? value : item));
      if (value.archived && cid === id) chooseConversation(conversations.find(item => item.kind === 'main')!.conversation_id);
    } catch (e) { setError(String(e)); }
  }
  async function upload(file: File) {
    if (!project) return false;
    const projectId = pid; const data = new FormData(); data.append('file', file);
    try {
      const value = await request<Source>(researchBase(pid) + '/sources', { method: 'POST', body: data });
      if (selection.current.pid === projectId) { setSources(previous => [value, ...previous.filter(item => item.source_version_id !== value.source_version_id)]); setSourceIds(previous => [...new Set([...previous, value.source_version_id])].slice(0, 12)); }
    } catch (e) { setError(String(e)); }
    return false;
  }
  async function saveBudget() {
    if (!project) return;
    const { question, template, dimensions, as_of, region, output_requirements } = project.brief;
    try {
    const value = await request<Project>(workspaceBase + `/${pid}/settings`, json('PUT', { question, template, dimensions, as_of, region, output_requirements, title: project.title, expected_revision: project.brief.project_revision, expected_settings_revision: project.settings_revision, budget, allow_network: project.preferences.allow_network, memory_enabled: project.preferences.memory_enabled }));
    setProjects(previous => previous.map(item => item.project_id === value.project_id ? value : item));
    return true;
    } catch (e) { setError(String(e)); return false; }
  }
  async function refreshMemories() { const projectId = pid; const values = await request<Memory[]>(researchBase(pid) + '/memories'); if (selection.current.pid === projectId) setMemories(values); }
  return { projects, project, pid, setPid, conversations, conversation, cid, chooseConversation, messages, sources, sourceIds, setSourceIds: (ids: string[]) => { sourceSelectionEdited.current = true; setSourceIds(ids); }, memories, run, runId, setRunId, text, setText: editText, replyTo, setReplyTo, prefill, mode, setMode, network, setNetwork, memoryEnabled, setMemoryEnabled, incremental, setIncremental, budget, setBudget, profiles, saveBudget, error, setError, sending, loading, projectBusy, refreshProjects, refreshMessages, refreshMemories, setProjects, send, cancel, addConversation, editConversation, upload, deleteProject, restoreProject };
}

import { apiBase } from './api';
function requestUrl(pid: string, rid: string) { return (apiBase + researchBase(pid) + `/runs/${rid}/events/ws`).replace(/^http/, 'ws'); }
export type WorkspaceController = ReturnType<typeof useWorkspace>;
