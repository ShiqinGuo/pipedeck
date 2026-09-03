import { Link, useParams } from '@tanstack/react-router';
import { ArrowLeft, Ban, RefreshCw, RotateCcw, Server } from 'lucide-react';
import { useMemo, useState } from 'react';

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
  const deployments = useDeployments(workspaceId ? { workspace_id: workspaceId } : {});
  const latest = deployments.data?.deployments[0];
  return (
    <section className="rounded-md border border-border bg-card" data-testid="runtime-state-panel">
      <header className="flex items-center gap-2 border-b border-border px-3 py-2">
        <Server className="size-4 text-muted-foreground" />
        <h3 className="text-xs font-semibold">Runtime 状态(部署侧)</h3>
        <Badge variant={latest ? (isDeploymentDegraded(latest.status) ? 'danger' : 'ok') : 'outline'}>
          {latest ? DEPLOYMENT_STATUS_LABELS[latest.status] : '无部署'}
        </Badge>
      </header>
      <div className="px-3 py-2 text-[11px] leading-relaxed text-muted-foreground">
        {deployments.isLoading ? (
          <span className="block h-4 w-40 animate-pulse rounded-sm bg-surface-3" />
        ) : deployments.isError ? (
          <span className="text-warn">无法读取部署状态:{deployments.error instanceof Error ? deployments.error.message : '未知错误'}</span>
        ) : !latest ? (
          <span>本次运行没有 Compose 部署;runtime 状态仅对部署型工作区展示。</span>
        ) : (
          <>
            <p>
              revision {latest.revision_id.slice(0, 12)} · {latest.project_name} · services {latest.services.join(', ') || '—'}
            </p>
            {latest.failure_detail && <p className="text-danger">{latest.failure_code}: {latest.failure_detail}</p>}
            {latest.recovery_detail && <p className="text-warn">恢复方式:{latest.recovery_detail}</p>}
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
  return (
    <section className="rounded-md border border-border bg-card" data-testid="log-step-group">
      <header className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
        <h4 className="font-mono text-xs font-semibold text-info">{stepId || '(无 step)'}</h4>
        <Badge variant="outline">{events.length} 条</Badge>
      </header>
      <div className="grid gap-0.5 px-3 py-2 font-mono text-[11px]" data-testid="run-log">
        {events.map((event) => (
          <p key={event.sequence} className="flex min-w-0 gap-2">
            <span className="shrink-0 text-[#5f6a64]">{formatTime(event.created_at)}</span>
            <span className={cn('shrink-0', event.kind === 'stderr' ? 'text-danger' : 'text-[#8d9891]')}>
              [{RUN_EVENT_KIND_LABELS[event.kind] ?? event.kind}]
            </span>
            <span className="min-w-0 whitespace-pre-wrap break-words text-foreground/90">{event.message}</span>
          </p>
        ))}
      </div>
    </section>
  );
}

export default function RunDetailRoute() {
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
        title={run.data ? `运行 ${run.data.id}` : '运行详情'}
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
                返回列表
              </Link>
            </Button>
            <Button
              variant="secondary"
              size="sm"
              disabled={!active || cancelRun.isPending}
              title={!active ? '只有排队中或运行中的记录可以取消' : cancelRun.isPending ? '正在取消' : '取消当前运行'}
              onClick={() => cancelRun.mutate(runId)}
            >
              {cancelRun.isPending ? <BusyLabel>取消中</BusyLabel> : <Ban />}
              取消
            </Button>
            <Button
              size="sm"
              disabled={!finished || retryRun.isPending}
              title={!finished ? '只能重试已结束的运行' : retryRun.isPending ? '正在重试' : '重新预检并重试'}
              onClick={() => retryRun.mutate(runId)}
            >
              {retryRun.isPending ? <BusyLabel>重试中</BusyLabel> : <RotateCcw />}
              重试
            </Button>
          </>
        }
      />
      <PageBody>
        {run.isError && <ErrorState error={run.error} onRetry={() => void run.refetch()} title="无法读取运行详情" />}

        {run.data?.failure_code && (
          <ErrorState
            error={new Error(`${run.data.failure_code}: ${run.data.failure_detail ?? '运行失败'}`)}
            title="运行失败"
          />
        )}
        {cancelRun.isError && <ErrorState error={cancelRun.error} title="取消失败" />}
        {retryRun.isError && <ErrorState error={retryRun.error} title="重试失败" />}

        {/* 双状态:执行状态面板 */}
        <section className="rounded-md border border-border bg-card" data-testid="execution-state-panel">
          <header className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
            <RefreshCw className={cn('size-4 text-muted-foreground', active && 'is-spinning')} />
            <h3 className="text-xs font-semibold">执行状态(门禁/构建进行到哪)</h3>
            {run.data?.current_step && <Badge variant="info">当前 step:{run.data.current_step}</Badge>}
          </header>
          <div className="px-3 py-2 text-[11px] text-muted-foreground">
            {run.data ? (
              <p>
                创建 {formatTime(run.data.created_at)} · 开始 {formatTime(run.data.started_at)} · 结束 {formatTime(run.data.finished_at)}
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
          <ErrorState error={events.error} onRetry={() => void events.refetch()} title="无法读取运行日志" />
        ) : grouped.length === 0 ? (
          <EmptyState icon={RefreshCw} title="还没有日志" detail={active ? '运行已启动,日志即将流入' : '此运行没有产生事件日志'} />
        ) : (
          <section className="grid gap-3">
            <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="按 step 过滤日志">
              <Button variant={stepFilter === null ? 'default' : 'secondary'} size="sm" onClick={() => setStepFilter(null)}>
                全部
              </Button>
              {stepIds.map((stepId) => (
                <Button key={stepId} variant={stepFilter === stepId ? 'default' : 'secondary'} size="sm" onClick={() => setStepFilter(stepId)}>
                  {stepId}
                </Button>
              ))}
            </div>
            {visibleGroups.map(([stepId, stepEvents]) => (
              <StepGroup key={stepId} stepId={stepId} events={stepEvents} />
            ))}
          </section>
        )}
        <CliFooter command={cli.logs(runId)} hint="等价 CLI:按 job 分组日志" />
      </PageBody>
    </PageScroll>
  );
}
