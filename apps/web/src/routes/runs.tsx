import { Link } from '@tanstack/react-router';
import { Play } from 'lucide-react';

import type { components } from '@/api/schema';
import { useRuns } from '@/api/hooks';
import { CliFooter } from '@/components/cli-command';
import { PageBody, PageHeader, PageScroll } from '@/components/page';
import { RunStatusPill } from '@/components/status-pill';
import { EmptyState, ErrorState, ListSkeleton } from '@/components/states';
import { cli } from '@/lib/cli';
import { formatDate } from '@/lib/utils';

type RunRecord = components['schemas']['RunRecord'];

function RunListRow({ run }: { run: RunRecord }) {
  return (
    <Link
      to="/runs/$runId"
      params={{ runId: run.id }}
      className="grid gap-x-4 gap-y-1 border-b border-[#252c28] px-3 py-2.5 hover:bg-surface-2 md:grid-cols-[minmax(0,1fr)_auto_auto]"
      data-testid="run-row"
    >
      <span className="min-w-0">
        <span className="block truncate text-xs font-semibold">{run.workspace_name}</span>
        <span className="block truncate font-mono text-[11px] text-muted-foreground">
          {run.id} · 开始 {formatDate(run.started_at)} · 结束 {formatDate(run.finished_at)}
        </span>
        {run.failure_detail && <span className="block truncate text-[11px] text-danger">{run.failure_code}: {run.failure_detail}</span>}
      </span>
      <span className="self-center font-mono text-[11px] text-muted-foreground">{run.current_step ?? '—'}</span>
      <span className="self-center">
        <RunStatusPill status={run.status} />
      </span>
    </Link>
  );
}

export default function RunsRoute() {
  const runs = useRuns();
  const records = runs.data?.runs ?? [];
  return (
    <PageScroll>
      <PageHeader eyebrow="RUNS" title="运行记录" description="按时间倒序的本地运行历史;运行中记录 3-4s 自动刷新。" />
      <PageBody>
        {runs.isLoading ? (
          <ListSkeleton rows={5} />
        ) : runs.isError ? (
          <ErrorState error={runs.error} onRetry={() => void runs.refetch()} title="无法读取运行记录" />
        ) : records.length === 0 ? (
          <EmptyState
            icon={Play}
            title="还没有运行记录"
            detail="到仓库管道页预览 .gitlab-ci.yml 并发起第一次运行;或到工作区发起工作区运行"
          />
        ) : (
          <div className="overflow-hidden rounded-md border border-border bg-card">
            {records.map((run) => (
              <RunListRow key={run.id} run={run} />
            ))}
          </div>
        )}
        <CliFooter command={cli.runs()} hint="等价 CLI:运行列表" />
      </PageBody>
    </PageScroll>
  );
}
