import { Activity, Container, GitPullRequestArrow, Home, KeyRound, LockKeyhole, RefreshCw, Workflow } from 'lucide-react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { api } from './api';
import type { View } from './app-types';
import tripguruMark from './assets/tripguru-local-mark.svg';
import { StatusDot } from './ui';
import { useApiToken } from './use-api-token';
import { OverviewView } from './views/OverviewView';
import { RepositoriesView } from './views/RepositoriesView';
import { ResourcesView } from './views/ResourcesView';
import { RunsView } from './views/RunsView';
import { WorkspacesView } from './views/WorkspacesView';

const NAV_ITEMS = [
  { id: 'overview', label: '总览', icon: Home },
  { id: 'workspaces', label: '工作区', icon: Workflow },
  { id: 'repositories', label: '仓库', icon: GitPullRequestArrow },
  { id: 'runs', label: '运行', icon: Activity },
  { id: 'resources', label: '资源', icon: Container },
] satisfies { id: View; label: string; icon: typeof Home }[];

const VIEW_LABELS: Record<View, string> = {
  overview: '本地总览',
  workspaces: '工作区',
  repositories: '仓库',
  runs: '运行记录',
  resources: '本地资源',
};

function AppNavigation({ view, onChange }: { view: View; onChange: (view: View) => void }) {
  return <aside className="app-nav"><div className="brand-mark" aria-label="TripGuru Local"><img className="brand-icon" src={tripguruMark} alt="" /></div><nav aria-label="主导航">{NAV_ITEMS.map((item) => { const Icon = item.icon; return <button type="button" key={item.id} className={view === item.id ? 'nav-item is-active' : 'nav-item'} aria-label={item.label} aria-current={view === item.id ? 'page' : undefined} title={item.label} onClick={() => onChange(item.id)}><Icon size={19} /><span>{item.label}</span></button>; })}</nav><div className="nav-foot"><LockKeyhole size={16} /><span>仅限本机</span></div></aside>;
}

function AppHeader({ view }: { view: View }) {
  const queryClient = useQueryClient();
  const overview = useQuery({ queryKey: ['overview'], queryFn: api.overview });
  const token = useApiToken();
  const refreshing = queryClient.isFetching() > 0;
  const controlState = overview.isError ? 'offline' : overview.data?.api_status === 'ready' ? 'ready' : 'loading';
  const controlLabel = controlState === 'ready' ? '本地控制服务' : controlState === 'offline' ? '控制服务离线' : '正在连接控制服务';
  return <header className="app-header"><div className="app-context"><strong>TripGuru Local</strong><span>/</span><span>{VIEW_LABELS[view]}</span></div><div className="header-actions"><div className={token.configured ? 'token-state is-ready' : 'token-state'} title={token.disabledReason ?? '写入令牌已加载'}>{token.configured ? <KeyRound size={14} /> : <LockKeyhole size={14} />}<span>{token.loading ? '读取权限' : token.configured ? '本地写入' : '只读'}</span></div><div className="runtime-identity"><StatusDot active={controlState === 'ready'} /><span>{controlLabel}</span>{controlState === 'ready' && <code>127.0.0.1:7421</code>}</div><button className="icon-button" type="button" aria-label="刷新全部本地状态" title="刷新全部本地状态" disabled={refreshing} onClick={() => void queryClient.invalidateQueries()}><RefreshCw size={16} className={refreshing ? 'is-spinning' : ''} /></button></div></header>;
}

export function App() {
  const [view, setView] = useState<View>('overview');
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState<string | null>(null);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const openWorkspace = (workspaceId: string) => { setSelectedWorkspaceId(workspaceId); setView('workspaces'); };
  const openRun = (runId: string) => { setSelectedRunId(runId); setView('runs'); };
  return <div className="app-shell"><AppNavigation view={view} onChange={setView} /><div className="app-main"><AppHeader view={view} /><main>{view === 'overview' ? <OverviewView onNavigate={setView} onOpenWorkspace={openWorkspace} onOpenRun={openRun} /> : view === 'workspaces' ? <WorkspacesView initialWorkspaceId={selectedWorkspaceId} onOpenRun={openRun} /> : view === 'repositories' ? <RepositoriesView /> : view === 'runs' ? <RunsView initialRunId={selectedRunId} onSelectedRun={setSelectedRunId} /> : <ResourcesView onOpenRun={openRun} />}</main></div></div>;
}
