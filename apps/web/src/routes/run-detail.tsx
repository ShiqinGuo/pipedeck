import { Link, useNavigate, useParams } from '@tanstack/react-router';
import { ArrowLeft, Ban, RefreshCw, RotateCcw } from 'lucide-react';
import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { components } from '@/api/schema';
import { useCancelRun, useRetryRun, useRun } from '@/api/hooks';
import { useRunEventStream } from '@/api/run-event-stream';
import { CliFooter } from '@/components/cli-command';
import { PageBody, PageHeader, PageScroll } from '@/components/page';
import { RunStatusPill } from '@/components/status-pill';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { BusyLabel, EmptyState, ErrorState, ListSkeleton } from '@/components/states';
import { RUN_EVENT_KIND_LABELS } from '@/lib/status';
import { WorkspaceRuntime } from '@/components/workspace-runtime';
import { cli } from '@/lib/cli';
import { cn, formatTime } from '@/lib/utils';

type RunEvent = components['schemas']['RunEvent'];

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
  const navigate = useNavigate();
  const run = useRun(runId);
  const events = useRunEventStream(runId, run.data?.status);
  const cancelRun = useCancelRun();
  const retryRun = useRetryRun();
  const [stepFilter, setStepFilter] = useState<string | null>(null);

  const grouped = useMemo(() => {
    const map = new Map<string, RunEvent[]>();
    for (const event of events.data?.events ?? []) {
      const key = event.step_id ?? '(无 step)';
      const group = map.get(key);
      if (group) group.push(event);
      else map.set(key, [event]);
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
              onClick={() => retryRun.mutate(runId, { onSuccess: (retried) => {
                setStepFilter(null);
                void navigate({ to: '/runs/$runId', params: { runId: retried.id } });
              } })}
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

        {run.data?.workspace_id && <WorkspaceRuntime workspaceId={run.data.workspace_id} />}

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
