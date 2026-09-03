import { Link, useNavigate } from '@tanstack/react-router';
import { CircleCheck, CircleX, GitBranch, Play, Rocket, Workflow } from 'lucide-react';

import type { components } from '@/api/schema';
import { useApiToken, useCatalog, useOverview, useRepositories, useRuntime, useRuns, useSession, useWorkspaces } from '@/api/hooks';
import { CliFooter } from '@/components/cli-command';
import { PageBody, PageHeader, PageScroll } from '@/components/page';
import { RunStatusPill } from '@/components/status-pill';
import { EmptyState, ErrorState, ListSkeleton, StatusDot } from '@/components/states';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { cli } from '@/lib/cli';
import { formatDate } from '@/lib/utils';

type RunRecord = components['schemas']['RunRecord'];
type RepositoryRecord = components['schemas']['RepositoryRecord'];

/** doctor 检查项:Docker / Git / 写入 token */
function DoctorCheck({ label, state, recovery }: { label: string; state: 'loading' | 'ok' | 'fail'; recovery?: string | null }) {
  return (
    <li className="flex min-w-0 items-start gap-2 py-1.5 text-xs" data-testid={`doctor-${label}`}>
      {state === 'loading' && <span className="mt-0.5 size-3.5 shrink-0 animate-pulse rounded-full bg-surface-3" aria-label={`正在检测 ${label}`} />}
      {state === 'ok' && <CircleCheck className="mt-0.5 size-3.5 shrink-0 text-ok" aria-label={`${label} 正常`} />}
      {state === 'fail' && <CircleX className="mt-0.5 size-3.5 shrink-0 text-danger" aria-label={`${label} 不可用`} />}
      <span className="min-w-0">
        <span className="font-semibold text-foreground">{label}</span>
        {state === 'ok' && <span className="block text-[11px] text-ok">就绪</span>}
        {state === 'fail' && (
          <span className="block text-[11px] leading-relaxed text-warn">
            {recovery ?? '请检查本机环境后重试'}
            <span className="ml-1 text-[11px] text-[#7f8a83]">修复后此卡片会自动消失</span>
          </span>
        )}
        {state === 'loading' && <span className="block text-[11px] text-muted-foreground">正在检测</span>}
      </span>
    </li>
  );
}

/** 首跑引导卡:doctor 全绿(Docker/Git/写入令牌)后消失 */
function FirstRunCard() {
  const session = useSession();
  const runtime = useRuntime();
  const catalog = useCatalog();
  const gitOk = !catalog.isError && (catalog.data === undefined || catalog.data.errors.length === 0 || catalog.data.projects.length > 0);
  const allOk = Boolean(session.data?.write_enabled) && Boolean(runtime.data?.docker_available) && gitOk;
  if (session.isError || runtime.isError) {
    return (
      <Card className="border-[#754246]">
        <CardHeader>
          <CardTitle>首跑引导 · doctor 检查</CardTitle>
        </CardHeader>
        <CardContent>
          <ErrorState error={session.error ?? runtime.error} onRetry={() => void (session.refetch(), runtime.refetch())} title="无法完成 doctor 检查" />
        </CardContent>
      </Card>
    );
  }
  if (allOk) return null;
  return (
    <Card data-testid="first-run-card">
      <CardHeader className="flex-row items-center justify-between gap-2">
        <CardTitle className="flex items-center gap-2">
          <Rocket className="size-4 text-primary" />
          首跑引导 · doctor 检查
        </CardTitle>
        <span className="font-mono text-[10px] text-muted-foreground">GET /api/v1/session + /runtime</span>
      </CardHeader>
      <CardContent>
        <ul className="grid gap-x-6 sm:grid-cols-2 lg:grid-cols-3">
          <DoctorCheck label="Docker" state={runtime.isLoading ? 'loading' : runtime.data?.docker_available ? 'ok' : 'fail'} recovery={runtime.data?.recovery ?? '启动 Docker Desktop 后重试'} />
          <DoctorCheck label="Git" state={catalog.isLoading ? 'loading' : gitOk ? 'ok' : 'fail'} recovery={catalog.error instanceof Error ? '确认本机已安装 Git 并可访问扫描根目录' : (catalog.data?.errors[0] ?? null)} />
          <DoctorCheck label="写入令牌" state={session.isLoading ? 'loading' : session.data?.write_enabled ? 'ok' : 'fail'} recovery="通过桌面客户端启动,或设置 VITE_API_TOKEN" />
        </ul>
      </CardContent>
    </Card>
  );
}

function RunRow({ run }: { run: RunRecord }) {
  return (
    <Link
      to="/runs/$runId"
      params={{ runId: run.id }}
      className="grid min-h-13 grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 border-b border-[#252c28] px-3 py-2 hover:bg-surface-2"
      data-testid="recent-run-row"
    >
      <span className="min-w-0">
        <span className="block truncate text-xs font-semibold">{run.workspace_name}</span>
        <span className="block truncate font-mono text-[11px] text-muted-foreground">
          {run.id} · {formatDate(run.created_at)}
          {run.failure_detail ? ` · ${run.failure_detail}` : ''}
        </span>
      </span>
      <RunStatusPill status={run.status} />
    </Link>
  );
}

function RecentRunsCard() {
  const navigate = useNavigate();
  const runs = useRuns();
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between gap-2">
        <CardTitle>最近运行</CardTitle>
        <Button variant="ghost" size="sm" onClick={() => void navigate({ to: '/runs' })}>
          <Play />全部运行
        </Button>
      </CardHeader>
      <CardContent className="px-0">
        {runs.isLoading ? (
          <ListSkeleton rows={3} className="px-3.5" />
        ) : runs.isError ? (
          <div className="px-3.5">
            <ErrorState error={runs.error} onRetry={() => void runs.refetch()} title="无法读取运行记录" />
          </div>
        ) : (runs.data?.runs.length ?? 0) === 0 ? (
          <div className="px-3.5">
            <EmptyState icon={Play} title="还没有运行记录" detail="先到仓库页导入一个包含 .gitlab-ci.yml 的仓库,预览管道后即可发起第一次运行" />
          </div>
        ) : (
          runs.data?.runs.slice(0, 5).map((run) => <RunRow key={run.id} run={run} />)
        )}
      </CardContent>
    </Card>
  );
}

function EnvironmentRow({ repository }: { repository: RepositoryRecord }) {
  return (
    <Link
      to="/pipelines/$repoId"
      params={{ repoId: repository.id }}
      className="grid min-h-13 grid-cols-[minmax(0,1fr)_auto] items-center gap-3 border-b border-[#252c28] px-3 py-2 hover:bg-surface-2"
    >
      <span className="min-w-0">
        <span className="block truncate text-xs font-semibold">{repository.name}</span>
        <span className="block truncate font-mono text-[11px] text-muted-foreground">
          <GitBranch className="mr-1 inline size-3" />
          {repository.branch} · {repository.head_sha.slice(0, 8)}
          {repository.dirty ? ' · 有未提交修改' : ''}
        </span>
      </span>
      <span className="font-mono text-[11px] text-muted-foreground">{repository.dirty ? 'DIRTY' : 'CLEAN'}</span>
    </Link>
  );
}

function ActiveEnvironmentsCard() {
  const workspaces = useWorkspaces();
  const repositories = useRepositories();
  const token = useApiToken();
  const workspaceCount = workspaces.data?.workspaces.length ?? 0;
  const repositoriesCount = repositories.data?.repositories.length ?? 0;
  const showWorkspaces = workspaceCount > 0;
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between gap-2">
        <CardTitle>活跃环境</CardTitle>
        <span className="font-mono text-[10px] text-muted-foreground">{workspaceCount} 工作区 · {repositoriesCount} 仓库</span>
      </CardHeader>
      <CardContent className="px-0">
        {workspaces.isLoading || repositories.isLoading ? (
          <ListSkeleton rows={2} className="px-3.5" />
        ) : (workspaces.isError || repositories.isError) ? (
          <div className="px-3.5">
            <ErrorState error={workspaces.error ?? repositories.error} onRetry={() => void (workspaces.refetch(), repositories.refetch())} title="无法读取环境状态" />
          </div>
        ) : showWorkspaces ? (
          workspaces.data?.workspaces.map((workspace) => (
            <Link
              key={workspace.id}
              to="/workspaces/$id"
              params={{ id: workspace.id }}
              className="grid min-h-13 grid-cols-[minmax(0,1fr)_auto] items-center gap-3 border-b border-[#252c28] px-3 py-2 hover:bg-surface-2"
              data-testid="active-workspace-row"
            >
              <span className="min-w-0">
                <span className="block truncate text-xs font-semibold">{workspace.name}</span>
                <span className="block truncate text-[11px] text-muted-foreground">{workspace.services.length} 服务 · rev {workspace.revision}</span>
              </span>
              <Workflow className="size-4 text-muted-foreground" />
            </Link>
          ))
        ) : (repositories.data?.repositories.length ?? 0) > 0 ? (
          repositories.data?.repositories.slice(0, 5).map((repository) => <EnvironmentRow key={repository.id} repository={repository} />)
        ) : (
          <div className="px-3.5">
            <EmptyState
              icon={GitBranch}
              title={token.configured ? '还没有环境' : '当前为只读模式'}
              detail="到仓库页导入或克隆仓库以开始;写操作需要本地写入令牌"
            />
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function OverviewMetrics() {
  const overview = useOverview();
  if (overview.isLoading) return <ListSkeleton rows={1} />;
  if (overview.isError) return <ErrorState error={overview.error} onRetry={() => void overview.refetch()} title="无法读取概览" />;
  const data = overview.data;
  if (!data) return null;
  const items = [
    { label: '项目', value: data.project_count },
    { label: '未提交修改', value: data.dirty_project_count },
    { label: '中间件', value: data.middleware_count },
    { label: '保护类型', value: data.protected_kinds.length },
  ];
  return (
    <div className="grid grid-cols-2 gap-px overflow-hidden rounded-md border border-border bg-border sm:grid-cols-4" data-testid="overview-metrics">
      {items.map((item) => (
        <div key={item.label} className="flex min-h-16 flex-col justify-center gap-1 bg-surface px-3.5">
          <span className="text-[11px] text-muted-foreground">{item.label}</span>
          <span className="font-mono text-sm font-medium">{item.value}</span>
        </div>
      ))}
      <div className="col-span-2 flex min-h-16 items-center gap-2 bg-surface px-3.5 sm:col-span-4">
        <StatusDot active={data.docker_available} />
        <span className="text-[11px] text-muted-foreground">
          Docker {data.docker_available ? '可用' : '不可用'} · {formatDate(data.generated_at)}
        </span>
      </div>
    </div>
  );
}

export default function DashboardRoute() {
  return (
    <PageScroll>
      <PageHeader
        eyebrow="DASHBOARD"
        title="本地开发总览"
        description="导入仓库 → 识别 .gitlab-ci.yml → 预览管道,三步内可运行第一个 job。"
      />
      <PageBody>
        <FirstRunCard />
        <OverviewMetrics />
        <div className="grid gap-4 lg:grid-cols-2">
          <RecentRunsCard />
          <ActiveEnvironmentsCard />
        </div>
        <CliFooter command={cli.status()} hint="等价 CLI:查看本地总览" />
      </PageBody>
    </PageScroll>
  );
}
