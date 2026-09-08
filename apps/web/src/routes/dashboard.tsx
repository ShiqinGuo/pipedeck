import { Link, useNavigate } from '@tanstack/react-router';
import { CircleCheck, CircleX, GitBranch, Play, Rocket, Workflow } from 'lucide-react';
import { useTranslation } from 'react-i18next';

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
  const { t } = useTranslation();
  return (
    <li className="flex min-w-0 items-start gap-2 py-1.5 text-xs" data-testid={`doctor-${label}`}>
      {state === 'loading' && <span className="mt-0.5 size-3.5 shrink-0 animate-pulse rounded-full bg-surface-3" aria-label={t('dashboard.doctor.checking', { label })} />}
      {state === 'ok' && <CircleCheck className="mt-0.5 size-3.5 shrink-0 text-ok" aria-label={t('dashboard.doctor.ok', { label })} />}
      {state === 'fail' && <CircleX className="mt-0.5 size-3.5 shrink-0 text-danger" aria-label={t('dashboard.doctor.unavailable', { label })} />}
      <span className="min-w-0">
        <span className="font-semibold text-foreground">{label}</span>
        {state === 'ok' && <span className="block text-[11px] text-ok">{t('dashboard.doctor.ready')}</span>}
        {state === 'fail' && (
          <span className="block text-[11px] leading-relaxed text-warn">
            {recovery ?? t('dashboard.doctor.retryHint')}
            <span className="ml-1 text-[11px] text-[#bfbfc3]">{t('dashboard.doctor.autoDismiss')}</span>
          </span>
        )}
        {state === 'loading' && <span className="block text-[11px] text-muted-foreground">{t('dashboard.doctor.checkingText')}</span>}
      </span>
    </li>
  );
}

/** 首跑引导卡:doctor 全绿(Docker/Git/写入令牌)后消失 */
function FirstRunCard() {
  const { t } = useTranslation();
  const session = useSession();
  const runtime = useRuntime();
  const catalog = useCatalog();
  const gitOk = !catalog.isError && (catalog.data === undefined || catalog.data.errors.length === 0 || catalog.data.projects.length > 0);
  const allOk = Boolean(session.data?.write_enabled) && Boolean(runtime.data?.docker_available) && gitOk;
  if (session.isError || runtime.isError) {
    return (
      <Card className="border-[#754246]">
        <CardHeader>
          <CardTitle>{t('dashboard.firstRun.title')}</CardTitle>
        </CardHeader>
        <CardContent>
          <ErrorState error={session.error ?? runtime.error} onRetry={() => void (session.refetch(), runtime.refetch())} title={t('dashboard.firstRun.doctorErrorTitle')} />
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
          {t('dashboard.firstRun.title')}
        </CardTitle>
        <span className="font-mono text-[10px] text-muted-foreground">GET /api/v1/session + /runtime</span>
      </CardHeader>
      <CardContent>
        <ul className="grid gap-x-6 sm:grid-cols-2 lg:grid-cols-3">
          <DoctorCheck label="Docker" state={runtime.isLoading ? 'loading' : runtime.data?.docker_available ? 'ok' : 'fail'} recovery={runtime.data?.recovery ?? t('dashboard.doctor.dockerRecovery')} />
          <DoctorCheck label="Git" state={catalog.isLoading ? 'loading' : gitOk ? 'ok' : 'fail'} recovery={catalog.error instanceof Error ? t('dashboard.doctor.gitRecovery') : (catalog.data?.errors[0] ?? null)} />
          <DoctorCheck label={t('dashboard.doctor.tokenLabel')} state={session.isLoading ? 'loading' : session.data?.write_enabled ? 'ok' : 'fail'} recovery={t('dashboard.doctor.tokenRecovery')} />
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
  const { t } = useTranslation();
  const navigate = useNavigate();
  const runs = useRuns();
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between gap-2">
        <CardTitle>{t('dashboard.recentRuns.title')}</CardTitle>
        <Button variant="ghost" size="sm" onClick={() => void navigate({ to: '/runs' })}>
          <Play />{t('dashboard.recentRuns.allRuns')}
        </Button>
      </CardHeader>
      <CardContent className="px-0">
        {runs.isLoading ? (
          <ListSkeleton rows={3} className="px-3.5" />
        ) : runs.isError ? (
          <div className="px-3.5">
            <ErrorState error={runs.error} onRetry={() => void runs.refetch()} title={t('dashboard.recentRuns.errorTitle')} />
          </div>
        ) : (runs.data?.runs.length ?? 0) === 0 ? (
          <div className="px-3.5">
            <EmptyState icon={Play} title={t('dashboard.recentRuns.emptyTitle')} detail={t('dashboard.recentRuns.emptyDetail')} />
          </div>
        ) : (
          runs.data?.runs.slice(0, 5).map((run) => <RunRow key={run.id} run={run} />)
        )}
      </CardContent>
    </Card>
  );
}

function EnvironmentRow({ repository }: { repository: RepositoryRecord }) {
  const { t } = useTranslation();
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
          {repository.dirty ? ` · ${t('dashboard.environments.dirty')}` : ''}
        </span>
      </span>
      <span className="font-mono text-[11px] text-muted-foreground">{repository.dirty ? 'DIRTY' : 'CLEAN'}</span>
    </Link>
  );
}

function ActiveEnvironmentsCard() {
  const { t } = useTranslation();
  const workspaces = useWorkspaces();
  const repositories = useRepositories();
  const token = useApiToken();
  const workspaceCount = workspaces.data?.workspaces.length ?? 0;
  const repositoriesCount = repositories.data?.repositories.length ?? 0;
  const showWorkspaces = workspaceCount > 0;
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between gap-2">
        <CardTitle>{t('dashboard.activeEnvironments.title')}</CardTitle>
        <span className="font-mono text-[10px] text-muted-foreground">{t('dashboard.activeEnvironments.counts', { workspaces: workspaceCount, repositories: repositoriesCount })}</span>
      </CardHeader>
      <CardContent className="px-0">
        {workspaces.isLoading || repositories.isLoading ? (
          <ListSkeleton rows={2} className="px-3.5" />
        ) : (workspaces.isError || repositories.isError) ? (
          <div className="px-3.5">
            <ErrorState error={workspaces.error ?? repositories.error} onRetry={() => void (workspaces.refetch(), repositories.refetch())} title={t('dashboard.activeEnvironments.errorTitle')} />
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
                <span className="block truncate text-[11px] text-muted-foreground">{t('dashboard.activeEnvironments.serviceCount', { count: workspace.services.length, revision: workspace.revision })}</span>
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
              title={token.configured ? t('dashboard.activeEnvironments.emptyTitle') : t('dashboard.activeEnvironments.readonlyTitle')}
              detail={t('dashboard.activeEnvironments.emptyDetail')}
            />
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function OverviewMetrics() {
  const { t } = useTranslation();
  const overview = useOverview();
  if (overview.isLoading) return <ListSkeleton rows={1} />;
  if (overview.isError) return <ErrorState error={overview.error} onRetry={() => void overview.refetch()} title={t('dashboard.metrics.errorTitle')} />;
  const data = overview.data;
  if (!data) return null;
  const items = [
    { label: t('dashboard.metrics.projects'), value: data.project_count },
    { label: t('dashboard.metrics.dirtyProjects'), value: data.dirty_project_count },
    { label: t('dashboard.metrics.middleware'), value: data.middleware_count },
    { label: t('dashboard.metrics.protectedKinds'), value: data.protected_kinds.length },
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
          {t('dashboard.metrics.dockerStatus', {
            status: data.docker_available ? t('dashboard.metrics.available') : t('dashboard.metrics.unavailable'),
            date: formatDate(data.generated_at),
          })}
        </span>
      </div>
    </div>
  );
}

export default function DashboardRoute() {
  const { t } = useTranslation();
  return (
    <PageScroll>
      <PageHeader
        eyebrow="DASHBOARD"
        title={t('dashboard.title')}
        description={t('dashboard.description')}
      />
      <PageBody>
        <FirstRunCard />
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-border bg-card p-4">
          <p className="text-sm">{t('dashboard.description')}</p>
          <Button asChild><Link to="/workspaces"><Workflow />{t('components.nav.workspaces')}</Link></Button>
        </div>
        <OverviewMetrics />
        <div className="grid gap-4 lg:grid-cols-2">
          <ActiveEnvironmentsCard />
          <RecentRunsCard />
        </div>
        <CliFooter command={cli.status()} hint={t('dashboard.cliHint')} />
      </PageBody>
    </PageScroll>
  );
}
