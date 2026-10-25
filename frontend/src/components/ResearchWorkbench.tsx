import { useEffect, useState } from 'react';
import { ConfigProvider, Drawer, Spin, theme } from 'antd';
import { useWorkspace } from './workspace/useWorkspace';
import { ProjectSidebar } from './workspace/ProjectSidebar';
import { ResearchConversation } from './workspace/ResearchConversation';
import { ExecutionInspector } from './workspace/ExecutionInspector';
import { WorkspaceDialogs } from './workspace/WorkspaceDialogs';
import type { Run, Mode } from './workspace/types';

export function ResearchWorkbench() {
  const w = useWorkspace();
  const [panel, setPanel] = useState<'create' | 'settings' | 'sources' | 'memories' | null>(null);
  const [sourceId, setSourceId] = useState<string | null>(null);
  const [report, setReport] = useState<Run | null>(null);
  const [projectsOpen, setProjectsOpen] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [compact, setCompact] = useState(() => window.matchMedia('(max-width: 1240px)').matches);
  const [mobileInspector, setMobileInspector] = useState(false);
  useEffect(() => { const media = window.matchMedia('(max-width: 1240px)'); const update = () => setCompact(media.matches); media.addEventListener('change', update); return () => media.removeEventListener('change', update); }, []);
  useEffect(() => { setPanel(null); setReport(null); setSourceId(null); }, [w.pid]);
  const nextMode: Mode = w.mode === 'auto' ? (w.replyTo || w.messages.some(item => item.kind !== 'legacy' && item.run?.report && item.run.selected_finding_ids?.length) ? 'focused' : w.project?.brief.template === 'industry' ? 'overview' : 'quick') : w.mode;
  const showInspector = () => compact ? setMobileInspector(true) : setInspectorOpen(true);
  const sidebar = <ProjectSidebar w={w} openCreate={() => setPanel('create')} openSettings={() => setPanel('settings')} openSources={() => setPanel('sources')} openMemories={() => setPanel('memories')} afterNavigate={() => setProjectsOpen(false)} />;
  return <ConfigProvider theme={{ algorithm: theme.defaultAlgorithm, token: { colorPrimary: '#28634f', colorInfo: '#28634f', colorBgBase: '#ffffff', colorBgContainer: '#ffffff', colorTextBase: '#24382e', borderRadius: 9, fontFamily: '"Segoe UI", "Microsoft YaHei", sans-serif' } }}>
    <div className={'workspace-shell ' + (!compact && inspectorOpen ? 'with-inspector' : 'without-inspector')}>
      <aside className="workspace-sidebar">{sidebar}</aside>
      {w.loading ? <div className="workspace-loading"><Spin size="large" /></div> : <ResearchConversation w={w} openProjects={() => setProjectsOpen(true)} openInspector={showInspector} openSources={() => setPanel('sources')} openReport={setReport} openSource={setSourceId} />}
      {!compact && inspectorOpen && <aside className="workspace-right" aria-label="执行记录侧栏"><ExecutionInspector w={w} nextMode={nextMode} close={() => setInspectorOpen(false)} /></aside>}
    </div>
    <Drawer rootClassName="workspace-overlay" title="项目空间" placement="left" width={280} open={projectsOpen} onClose={() => setProjectsOpen(false)}>{sidebar}</Drawer>
    <Drawer rootClassName="workspace-overlay" title={null} width="min(360px, 96vw)" open={compact && mobileInspector} onClose={() => setMobileInspector(false)} styles={{ body: { padding: 0 } }}><ExecutionInspector w={w} nextMode={nextMode} close={() => setMobileInspector(false)} /></Drawer>
    <WorkspaceDialogs w={w} panel={panel} close={() => setPanel(null)} sourceId={sourceId} closeSource={() => setSourceId(null)} viewSource={setSourceId} report={report} closeReport={() => setReport(null)} />
  </ConfigProvider>;
}
