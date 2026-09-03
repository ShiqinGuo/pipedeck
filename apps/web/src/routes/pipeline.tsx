import { useParams } from '@tanstack/react-router';
import { AlertTriangle, ListChecks, Play, RefreshCw } from 'lucide-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import type { components } from '@/api/schema';
import { api } from '@/api/client';
import { usePipelinePreview, useRepositories } from '@/api/hooks';
import { CliCommand } from '@/components/cli-command';
import { PlanIssueList, PlanDialog } from '@/components/plan-dialog';
import { PageBody, PageHeader, PageScroll } from '@/components/page';
import { Badge } from '@/components/ui/badge';
import { BusyLabel, EmptyState, ErrorState } from '@/components/states';
import { Button } from '@/components/ui/button';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { cli } from '@/lib/cli';
import { jobImageLabel, jobKey, jobNeedsLabel } from '@/lib/pipeline';

type WorkspacePlanResponse = components['schemas']['WorkspacePlanResponse'];

export default function PipelineRoute() {
  const { repoId } = useParams({ from: '/pipelines/$repoId' });
  const [refresh, setRefresh] = useState(false);
  const [plan, setPlan] = useState<WorkspacePlanResponse | null>(null);
  const repositories = useRepositories();
  const preview = usePipelinePreview(repoId, refresh);
  const queryClient = useQueryClient();
  const repository = repositories.data?.repositories.find((candidate) => candidate.id === repoId);

  const planMutation = useMutation({
    mutationFn: (payload: { fetch_includes: boolean }) => api.createPipelinePlan(repoId, payload),
    onSuccess: setPlan,
  });
  const blockers = preview.data?.blockers ?? [];
  const hasBlockers = blockers.length > 0 || (preview.data ? !preview.data.ready : false);
  const planDisabledReason = !preview.data
    ? '预览尚未就绪'
    : hasBlockers
      ? '存在阻断项,修正 .gitlab-ci.yml 后重试'
      : planMutation.isPending
        ? '正在生成预检计划'
        : null;
  const refreshBusy = preview.isFetching;
  const repoForCli =
    repository ?? { id: repoId, name: repoId, path: repoId, origin_url: null, branch: '', head_sha: '', upstream: null, dirty: false, created_at: '', updated_at: '' };

  return (
    <PageScroll>
      <PageHeader
        eyebrow="PIPELINES"
        title={repository ? `${repository.name} · 管道预览` : '管道预览'}
        description="运行前看会跑什么:name/stage/needs/image/when、变量与阻断项。include 缓存可强制刷新。"
        actions={
          <>
            <Button
              variant="secondary"
              size="sm"
              title={refreshBusy ? '正在刷新' : '强制重新拉取 include 文件并刷新预览(?refresh=true)'}
              onClick={() => {
                setRefresh(true);
                void queryClient.invalidateQueries({ queryKey: ['pipeline-preview', repoId, true] });
              }}
            >
              {refreshBusy ? <RefreshCw className="is-spinning" /> : <RefreshCw />}
              强制刷新 include
            </Button>
            <Button
              size="sm"
              disabled={Boolean(planDisabledReason)}
              title={planDisabledReason ?? '生成预检计划并运行'}
              onClick={() => planMutation.mutate({ fetch_includes: refresh })}
            >
              {planMutation.isPending ? <BusyLabel>预检中</BusyLabel> : <Play />}
              运行
            </Button>
          </>
        }
      />
      <PageBody>
        {/* 预览加载/错误态 */}
        {preview.isLoading && (
          <div className="grid gap-2" aria-busy="true" aria-label="正在加载管道预览">
            {Array.from({ length: 3 }, (_, index) => (
              <div key={index} className="h-9 animate-pulse rounded-sm bg-surface-3" />
            ))}
          </div>
        )}
        {preview.isError && (
          <ErrorState
            error={preview.error}
            onRetry={() => void preview.refetch()}
            title="管道预览不可用"
          />
        )}

        {preview.data && (
          <>
            <PlanIssueList issues={preview.data.blockers} tone="blocker" />
            <PlanIssueList issues={preview.data.warnings} tone="warning" />

            {/* 阻断时也不隐藏 job 表:先看再跑 */}
            {(preview.data.jobs.length === 0 && preview.data.ready) ? (
              <EmptyState
                icon={ListChecks}
                title="没有可运行的 job"
                detail=".gitlab-ci.yml 已解析但没有展开出 job;检查 workflow: rules 或文件内容"
              />
            ) : (
              <div className="overflow-hidden rounded-md border border-border bg-card" data-testid="pipeline-job-table">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Job</TableHead>
                      <TableHead>Stage</TableHead>
                      <TableHead>Needs</TableHead>
                      <TableHead>Image</TableHead>
                      <TableHead>When</TableHead>
                      <TableHead>Flags</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {preview.data.jobs.map((job) => (
                      <TableRow key={jobKey(job)} data-testid="pipeline-job-row">
                        <TableCell className="font-mono font-semibold">{job.name}</TableCell>
                        <TableCell>
                          <Badge variant="info">{job.stage}</Badge>
                        </TableCell>
                        <TableCell className="font-mono text-[11px]">{jobNeedsLabel(job)}</TableCell>
                        <TableCell className="font-mono text-[11px]">{jobImageLabel(job)}</TableCell>
                        <TableCell>
                          <Badge variant={job.when === 'manual' ? 'warn' : 'outline'}>{job.when}</Badge>
                        </TableCell>
                        <TableCell>
                          <span className="flex flex-wrap gap-1">
                            {job.allow_failure && <Badge variant="outline">allow_failure</Badge>}
                            {!job.included && <Badge variant="outline">included=false</Badge>}
                            {job.unsupported.length > 0 && (
                              <Badge variant="danger" title={job.unsupported.join('; ')}>
                                未支持语义
                              </Badge>
                            )}
                          </span>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}

            {/* 变量展示 */}
            {Object.keys(preview.data.global_variables ?? {}).length > 0 && (
              <div className="rounded-md border border-border bg-card" data-testid="pipeline-variables">
                <header className="flex items-center gap-2 border-b border-border px-3 py-2">
                  <AlertTriangle className="size-3.5 text-muted-foreground" />
                  <span className="text-xs font-semibold">全局变量预览</span>
                  <span className="font-mono text-[10px] text-muted-foreground">
                    指纹 {preview.data.config_fingerprint?.slice(0, 12) ?? '—'}
                  </span>
                </header>
                <dl className="grid gap-1 px-3 py-2 font-mono text-[11px] sm:grid-cols-2 lg:grid-cols-3">
                  {Object.entries(preview.data.global_variables ?? {}).map(([name, value]) => (
                    <div key={name} className="flex min-w-0 gap-2">
                      <dt className="shrink-0 font-semibold text-info">{name}</dt>
                      <dd className="truncate text-muted-foreground">{value || '(空)'}</dd>
                    </div>
                  ))}
                </dl>
              </div>
            )}
          </>
        )}
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1.5 px-1 py-2">
          <CliCommand command={cli.pipelineList(repoForCli)} />
          <CliCommand command={cli.run(repoForCli)} />
          <span className="text-[11px] text-[#bfbfc3]">等价 CLI:管道预览 · 运行(--wait 阻塞到 Run 结束)</span>
        </div>
      </PageBody>

      {plan && <PlanDialog plan={plan} onClose={() => setPlan(null)} />}
    </PageScroll>
  );
}
