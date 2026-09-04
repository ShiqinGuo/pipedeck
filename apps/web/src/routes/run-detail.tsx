import { Link, useParams } from '@tanstack/react-router';
import { ArrowLeft, Ban, RefreshCw, RotateCcw, Server } from 'lucide-react';
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { components } from '@/api/schema';
import { api } from '@/api/client';
import { useApiQuery, useCancelRun, useDeployments, useRetryRun, useRun } from '@/api/hooks';
import { CliFooter } from '@/components/cli-command';
import { PageBody, PageHeader, PageScroll } from '@/components/page';
import { RunStatusPill } from '@/components/status-pill';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { BusyLabel, EmptyState, ErrorState, ListSkeleton } from '@/components/states';
import { DEPLOYMENT_STATUS_LABELS, RUN_EVENT_KIND_LABELS, isDeploymentDegraded } from '@/lib/status';
import { cli } from '@/lib/cli';
import { cn, formatTime } from '@/lib/utils';
import { queryKeys } from '@/api/queryKeys';

type RunEvent = components['schemas']['RunEvent'];
type RunEventListResponse = components['schemas']['RunEventListResponse'];

/** 增量事件流:queryFn 内按 after 游标循环拉齐(运行中 1.5s 轮询) */
function useRunEventStream(runId: string) {
  return useApiQuery<RunEventListResponse>({
    queryKey: queryKeys.runEvents(runId, 0),
    queryFn: async () => {
      let after = 0;
      let events: RunEvent[] = [];
      for (;;) {
        const page = await api.runEvents(runId, after);
        events = events.concat(page.events);
        if (page.events.length === 0 || page.next_after === after) break;
        after = page.next_after;
      }
      return { events, next_after: after };
    },
    enabled: Boolean(runId),
    staleTime: 1_000,
    refetchInterval: 1_500,
    refetchOnWindowFocus: false,
  });
}

/** runtime 状态(与执行状态分开显示) */
function RuntimeStatePanel({ workspaceId }: { workspaceId: string | null }) {
  const { t } = useTranslation();
  const deployments = useDeployments(workspaceId ? { workspace_id: workspaceId } : {});
  const latest = deployments.data?.deployments[0];
  return (
    <section className="rounded-md border border-border bg-card" data-testid="runtime-state-panel">
      <header className="flex items-center gap-2 border-b border-border px-3 py-2">
        <Server className="size-4 text-muted-foreground" />
        <h3 className="text-xs font-semibold">{t('runDetail.runtime.title')}</h3>
        <Badge variant={latest ? (isDeploymentDegraded(latest.status) ? 'danger' : 'ok') : 'outline'}>
          {latest ? t(DEPLOYMENT_STATUS_LABELS[latest.status]) : t('runDetail.runtime.noDeployment')}
        </Badge>
      </header>
      <div className="px-3 py-2 text-[11px] leading-relaxed text-muted-foreground">
        {deployments.isLoading ? (
          <span className="block h-4 w-40 animate-pulse rounded-sm bg-surface-3" />
        ) : deployments.isError ? (
          <span className="text-warn">{t('runDetail.runtime.deployError', { message: deployments.error instanceof Error ? deployments.error.message : t('runDetail.runtime.unknownError') })}</span>
        ) : !latest ? (
          <span>{t('runDetail.runtime.noCompose')}</span>
        ) : (
          <>
            <p>
              revision {latest.revision_id.slice(0, 12)} · {latest.project_name} · services {latest.services.join(', ') || '—'}
            </p>
            {latest.failure_detail && <p className="text-danger">{latest.failure_code}: {latest.failure_detail}</p>}
            {latest.recovery_detail && <p className="text-warn">{t('runDetail.runtime.recovery', { detail: latest.recovery_detail })}</p>}
          </>
        )}
      </div>
    </section>
  );
}

function StepGroup({
  stepId,
  events,
}: {
  stepId: string;
  events: RunEvent[];
}) {
  const { t } = useTranslation();
  return (
    <section className="rounded-md border border-border bg-card" data-testid="log-step-group">
      <header className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
        <h4 className="font-mono text-xs font-semibold text-info">{stepId || t('runDetail.step.noStep')}</h4>
        <Badge variant="outline">{t('runDetail.step.eventCount', { count: events.length })}</Badge>
      </header>
      <div className="grid gap-0.5 px-3 py-2 font-mono text-[11px]" data-testid="run-log">
        {events.map((event) => (
          <p key={event.sequence} className="flex min-w-0 gap-2">
            <span className="shrink-0 text-[#5f6a64]">{formatTime(event.created_at)}</span>
            <span className={cn('shrink-0', event.kind === 'stderr' ? 'text-danger' : 'text-[#bfbfc3]')}>
              [{t(RUN_EVENT_KIND_LABELS[event.kind] ?? event.kind)}]
            </span>
            <span className="min-w-0 whitespace-pre-wrap break-words text-foreground/90">{event.message}</span>
          </p>
        ))}
      </div>
    </section>
  );
}

export default function RunDetailRoute() {
  const { t } = useTranslation();
  const { runId } = useParams({ from: '/runs/$runId' });
  const run = useRun(runId);
  const events = useRunEventStream(runId);
  const cancelRun = useCancelRun();
  const retryRun = useRetryRun();
  const [stepFilter, setStepFilter] = useState<string | null>(null);

  const grouped = useMemo(() => {
    const map = new Map<string, RunEvent[]>();
    for (const event of events.data?.events ?? []) {
      const key = event.step_id ?? '(无 step)';
      map.set(key, [...(map.get(key) ?? []), event]);
    }
    return [...map.entries()];
  }, [events.data]);

  const stepIds = grouped.map(([stepId]) => stepId);
  const visibleGroups = stepFilter ? grouped.filter(([stepId]) => stepId === stepFilter) : grouped;
  const active = run.data ? run.data.status === 'queued' || run.data.status === 'running' : false;
  const finished = run.data ? !active : false;

  return (
    <PageScroll>
      <PageHeader
        eyebrow="RUN DETAIL"
        title={run.data ? t('runDetail.titleWithId', { id: run.data.id }) : t('runDetail.title')}
        compact
        description={
          run.data && (
            <span className="flex flex-wrap items-center gap-2">
              <RunStatusPill status={run.data.status} />
              <span className="font-mono text-[11px]">
                workspace {run.data.workspace_name} · plan {run.data.plan_id?.slice(0, 12) ?? '—'}
                {run.data.retry_of ? ` · retry of ${run.data.retry_of}` : ''}
              </span>
            </span>
          )
        }
        actions={
          <>
            <Button asChild variant="ghost" size="sm">
              <Link to="/runs">
                <ArrowLeft />
                {t('runDetail.backToList')}
              </Link>
            </Button>
            <Button
              variant="secondary"
              size="sm"
              disabled={!active || cancelRun.isPending}
              title={!active ? t('runDetail.cancel.notActive') : cancelRun.isPending ? t('runDetail.cancel.pending') : t('runDetail.cancel.action')}
              onClick={() => cancelRun.mutate(runId)}
            >
              {cancelRun.isPending ? <BusyLabel>{t('runDetail.cancel.pending')}</BusyLabel> : <Ban />}
              {t('runDetail.cancel.label')}
            </Button>
            <Button
              size="sm"
              disabled={!finished || retryRun.isPending}
              title={!finished ? t('runDetail.retry.notFinished') : retryRun.isPending ? t('runDetail.retry.pending') : t('runDetail.retry.action')}
              onClick={() => retryRun.mutate(runId)}
            >
              {retryRun.isPending ? <BusyLabel>{t('runDetail.retry.pending')}</BusyLabel> : <RotateCcw />}
              {t('runDetail.retry.label')}
            </Button>
          </>
        }
      />
      <PageBody>
        {run.isError && <ErrorState error={run.error} onRetry={() => void run.refetch()} title={t('runDetail.errorTitle')} />}

        {run.data?.failure_code && (
          <ErrorState
            error={new Error(`${run.data.failure_code}: ${run.data.failure_detail ?? t('runDetail.failed')}`)}
            title={t('runDetail.failed')}
          />
        )}
        {cancelRun.isError && <ErrorState error={cancelRun.error} title={t('runDetail.cancel.failed')} />}
        {retryRun.isError && <ErrorState error={retryRun.error} title={t('runDetail.retry.failed')} />}

        {/* 双状态:执行状态面板 */}
        <section className="rounded-md border border-border bg-card" data-testid="execution-state-panel">
          <header className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
            <RefreshCw className={cn('size-4 text-muted-foreground', active && 'is-spinning')} />
            <h3 className="text-xs font-semibold">{t('runDetail.execution.title')}</h3>
            {run.data?.current_step && <Badge variant="info">{t('runDetail.execution.currentStep', { step: run.data.current_step })}</Badge>}
          </header>
          <div className="px-3 py-2 text-[11px] text-muted-foreground">
            {run.data ? (
              <p>
                {t('runDetail.execution.timeline', { created: formatTime(run.data.created_at), started: formatTime(run.data.started_at), finished: formatTime(run.data.finished_at) })}
              </p>
            ) : (
              <span className="block h-4 w-56 animate-pulse rounded-sm bg-surface-3" />
            )}
          </div>
        </section>

        {run.data && <RuntimeStatePanel workspaceId={run.data.workspace_id} />}

        {/* 日志:按 step 分组 + 过滤 */}
        {events.isLoading ? (
          <ListSkeleton rows={3} />
        ) : events.isError ? (
          <ErrorState error={events.error} onRetry={() => void events.refetch()} title={t('runDetail.logs.errorTitle')} />
        ) : grouped.length === 0 ? (
          <EmptyState icon={RefreshCw} title={t('runDetail.logs.emptyTitle')} detail={active ? t('runDetail.logs.incoming') : t('runDetail.logs.noEvents')} />
        ) : (
          <section className="grid gap-3">
            <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label={t('runDetail.logs.filterLabel')}>
              <Button variant={stepFilter === null ? 'default' : 'secondary'} size="sm" onClick={() => setStepFilter(null)}>
                {t('runDetail.logs.all')}
              </Button>
              {stepIds.map((stepId) => (
                <Button key={stepId} variant={stepFilter === stepId ? 'default' : 'secondary'} size="sm" onClick={() => setStepFilter(stepId)}>
                  {stepId || t('runDetail.step.noStep')}
                </Button>
              ))}
            </div>
            {visibleGroups.map(([stepId, stepEvents]) => (
              <StepGroup key={stepId} stepId={stepId} events={stepEvents} />
            ))}
          </section>
        )}
        <CliFooter command={cli.logs(runId)} hint={t('runDetail.cliHint')} />
      </PageBody>
    </PageScroll>
  );
}
