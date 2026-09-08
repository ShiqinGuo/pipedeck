import { Link } from '@tanstack/react-router';
import { RefreshCw, Server } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { useWorkspaceRuntime } from '@/api/hooks';
import { ApplicationLink } from '@/components/application-link';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ErrorState, ListSkeleton } from '@/components/states';
import { formatTime } from '@/lib/utils';

export function WorkspaceRuntime({ workspaceId, revision }: { workspaceId: string; revision?: number }) {
  const { t } = useTranslation();
  const runtime = useWorkspaceRuntime(workspaceId);
  const data = runtime.data;
  return (
    <section className="min-w-0 rounded-md border border-border bg-card" data-testid="runtime-state-panel">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-3 py-2.5">
        <div className="flex flex-wrap items-center gap-2">
          <Server className="size-4 text-info" />
          <h2 className="text-sm font-semibold">{t('integration.title')}</h2>
          {data && <Badge variant={data.ready ? 'ok' : 'warn'}>{t('integration.count', { ready: data.ready_count, total: data.services.length })}</Badge>}
        </div>
        <Button variant="ghost" size="sm" onClick={() => void runtime.refetch()} disabled={runtime.isFetching} aria-label={t('integration.refresh')}>
          <RefreshCw className={runtime.isFetching ? 'is-spinning' : ''} />{t('integration.refresh')}
        </Button>
      </header>
      <div className="grid gap-3 p-3">
        <p className="text-xs leading-relaxed text-muted-foreground">{t('integration.description')}</p>
        {runtime.isLoading ? <ListSkeleton rows={2} /> : runtime.isError ? (
          <ErrorState error={runtime.error} onRetry={() => void runtime.refetch()} title={t('integration.error')} />
        ) : data && <>
          {data.services.some((service) => service.configured === false) && <p role="alert" className="text-xs text-warn">{t('integration.previousServices')}</p>}
          <ul className="grid gap-2" data-testid="integration-services">
            {data.services.map((service) => (
              <li key={service.project_id} className="grid min-w-0 gap-2 rounded-sm border border-border bg-surface-2 p-3 sm:grid-cols-[minmax(0,1fr)_auto]" data-testid="integration-service">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <strong className="min-w-0 break-all text-xs">{service.name}</strong>
                    {service.configured === false && <Badge variant="warn">{t('integration.previousConfig')}</Badge>}
                    <Badge variant={service.status === 'ready' ? 'ok' : service.status === 'unhealthy' ? 'danger' : 'outline'}>{t(`integration.status.${service.status}`)}</Badge>
                    <span className="text-[11px] text-muted-foreground">{service.target === 'compose' ? 'Docker Compose' : t('integration.host')}</span>
                  </div>
                  {(service.branch || service.head) && <p className="mt-1 break-all font-mono text-[11px] text-muted-foreground">{service.branch} · {service.head?.slice(0, 12) ?? '—'}{service.dirty ? ` · ${t('integration.dirty')}` : ''}</p>}
                  <p className="mt-1 break-words text-xs text-muted-foreground">{service.detail}</p>
                  {service.recovery && <p className="mt-1 break-words text-xs text-warn">{service.recovery}</p>}
                  {revision !== undefined && service.workspace_revision != null && revision !== service.workspace_revision && <p className="mt-1 text-xs text-warn">{t('integration.configChanged', { revision: service.workspace_revision })}</p>}
                  {service.url && <p className="mt-1 break-all font-mono text-[11px] text-muted-foreground">{service.url}</p>}
                </div>
                <div className="flex flex-wrap items-start gap-2">
                  {service.run_id && <Button asChild variant="secondary" size="sm"><Link to="/runs/$runId" params={{ runId: service.run_id }}>{t('integration.logs')}</Link></Button>}
                  {service.url && <ApplicationLink url={service.url} />}
                </div>
              </li>
            ))}
          </ul>
          {data.latest_run && <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span>{t('integration.latestRun')}</span><Link className="text-info underline" to="/runs/$runId" params={{ runId: data.latest_run.id }}>{data.latest_run.workspace_name}</Link>
            <span>{t(`integration.run.${data.latest_run.status}`)}</span>
            {data.latest_run.failure_detail && <span className="break-words text-danger">{data.latest_run.failure_detail}</span>}
          </div>}
          <p className="text-[11px] text-muted-foreground">{t('integration.observed', { time: formatTime(data.generated_at) })}</p>
        </>}
      </div>
    </section>
  );
}
