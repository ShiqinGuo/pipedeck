import { Link } from '@tanstack/react-router';
import { Play } from 'lucide-react';
import { useTranslation } from 'react-i18next';

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
  const { t } = useTranslation();
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
          {t('runs.row.timeRange', { id: run.id, started: formatDate(run.started_at), finished: formatDate(run.finished_at) })}
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
  const { t } = useTranslation();
  const runs = useRuns();
  const records = runs.data?.runs ?? [];
  return (
    <PageScroll>
      <PageHeader eyebrow="RUNS" title={t('runs.title')} description={t('runs.description')} />
      <PageBody>
        {runs.isLoading ? (
          <ListSkeleton rows={5} />
        ) : runs.isError ? (
          <ErrorState error={runs.error} onRetry={() => void runs.refetch()} title={t('runs.errorTitle')} />
        ) : records.length === 0 ? (
          <EmptyState
            icon={Play}
            title={t('runs.empty.title')}
            detail={t('runs.empty.detail')}
          />
        ) : (
          <div className="overflow-hidden rounded-md border border-border bg-card">
            {records.map((run) => (
              <RunListRow key={run.id} run={run} />
            ))}
          </div>
        )}
        <CliFooter command={cli.runs()} hint={t('runs.cliHint')} />
      </PageBody>
    </PageScroll>
  );
}
