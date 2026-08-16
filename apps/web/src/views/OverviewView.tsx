import { Activity, Container, FolderGit2, PackageSearch, PlayCircle, ServerCog, ShieldCheck, Workflow } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';

import { api } from '../api';
import type { View } from '../app-types';
import { EmptyState, ErrorState, LoadingRows, RUN_STATUS_LABELS, StatusPill, formatDate } from '../ui';

export function OverviewView({ onNavigate, onOpenWorkspace, onOpenRun }: { onNavigate: (view: View) => void; onOpenWorkspace: (workspaceId: string) => void; onOpenRun: (runId: string) => void }) {
  const overview = useQuery({ queryKey: ['overview'], queryFn: api.overview });
  const workspaces = useQuery({ queryKey: ['workspaces'], queryFn: api.workspaces });
  const runs = useQuery({ queryKey: ['runs'], queryFn: api.runs, refetchInterval: 4000 });
  const repositories = useQuery({ queryKey: ['repositories'], queryFn: api.repositories });
  const activeRuns = (runs.data?.runs ?? []).filter((run) => run.status === 'queued' || run.status === 'running');
  const recentRuns = (runs.data?.runs ?? []).slice(0, 6);

  return (
    <div className="page-scroll">
      <div className="page-heading"><div><span>LOCAL CONTROL PLANE</span><h1>本地开发总览</h1><p>工作区、运行与受保护资源的实时状态</p></div><button className="primary-button" type="button" onClick={() => onNavigate('workspaces')}><Workflow size={17} />打开工作区</button></div>
      {overview.isError ? <ErrorState error={overview.error} onRetry={() => void overview.refetch()} /> : (
        <section className="metric-strip" aria-label="本地环境摘要">
          <div><ServerCog size={19} /><span>控制服务</span><strong>{overview.isLoading ? '···' : '正常'}</strong></div>
          <div><Container size={19} /><span>Docker</span><strong>{overview.data?.docker_available ? '可用' : '不可用'}</strong></div>
          <div><FolderGit2 size={19} /><span>已保存工作区</span><strong>{workspaces.data?.workspaces.length ?? '—'}</strong></div>
          <div><Activity size={19} /><span>活动运行</span><strong>{activeRuns.length}</strong></div>
          <div><PackageSearch size={19} /><span>已接入仓库</span><strong>{repositories.data?.repositories.length ?? '—'}</strong></div>
          <div><ShieldCheck size={19} /><span>中间件实例</span><strong>{overview.data?.middleware_count ?? '—'}</strong></div>
        </section>
      )}
      <div className="overview-grid">
        <section className="data-band">
          <header><div><PlayCircle size={17} /><h2>最近运行</h2></div><button className="text-button" type="button" onClick={() => onNavigate('runs')}>查看全部</button></header>
          {runs.isError ? <ErrorState error={runs.error} onRetry={() => void runs.refetch()} /> : runs.isLoading ? <LoadingRows count={5} /> : recentRuns.length === 0 ? <EmptyState icon={PlayCircle} title="还没有运行记录" detail="从已保存工作区完成预检后启动" /> : (
            <div className="dense-rows">{recentRuns.map((run) => <button className="dense-row run-summary-row" type="button" key={run.id} onClick={() => onOpenRun(run.id)}><span className="row-icon"><Activity size={16} /></span><span className="row-main"><strong>{run.workspace_name}</strong><small>{run.current_step ?? (run.mode === 'integrated' ? '集成模式' : '开发模式')}</small></span><StatusPill status={run.status} /><time>{formatDate(run.created_at)}</time></button>)}</div>
          )}
        </section>
        <section className="data-band">
          <header><div><Workflow size={17} /><h2>工作区</h2></div><button className="text-button" type="button" onClick={() => onNavigate('workspaces')}>管理</button></header>
          {workspaces.isError ? <ErrorState error={workspaces.error} onRetry={() => void workspaces.refetch()} /> : workspaces.isLoading ? <LoadingRows count={5} /> : (workspaces.data?.workspaces.length ?? 0) === 0 ? <EmptyState icon={Workflow} title="没有已保存工作区" detail="组合前后端项目与本地中间件" /> : (
            <div className="dense-rows">{workspaces.data?.workspaces.slice(0, 6).map((workspace) => <button className="dense-row workspace-summary-row" type="button" key={workspace.id} onClick={() => onOpenWorkspace(workspace.id)}><span className="row-icon"><Workflow size={16} /></span><span className="row-main"><strong>{workspace.name}</strong><small>{workspace.services.length} 个服务 · revision {workspace.revision}</small></span><span className="mode-label">{workspace.mode === 'integrated' ? '集成' : '开发'}</span><time>{formatDate(workspace.updated_at)}</time></button>)}</div>
          )}
        </section>
      </div>
      {(runs.isError || workspaces.isError || repositories.isError) && <div className="page-foot-note">部分本地状态暂不可用；各区块可独立重试，不使用伪造数据。</div>}
      <span className="sr-only">{Object.values(RUN_STATUS_LABELS).join('、')}</span>
    </div>
  );
}
