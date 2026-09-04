import { useParams } from '@tanstack/react-router';
import { AlertTriangle, FileCode2, FileCog, ListChecks, Play, Plus, RefreshCw, Save } from 'lucide-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';

import type { components } from '@/api/schema';
import { api } from '@/api/client';
import {
  usePipelineFileContent,
  usePipelineFiles,
  usePipelinePreview,
  useRepositories,
  useSavePipelineFile,
  useSelectPipelineFile,
} from '@/api/hooks';
import { CliCommand } from '@/components/cli-command';
import { PlanIssueList, PlanDialog } from '@/components/plan-dialog';
import { PageBody, PageHeader, PageScroll } from '@/components/page';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { BusyLabel, EmptyState, ErrorState } from '@/components/states';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Textarea } from '@/components/ui/textarea';
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
        <PipelineFileCard repositoryId={repoId} />
        <DeclarationCard repositoryId={repoId} />

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

/**
 * 管道文件可配置化:仓库内任意 YAML 都可作为本地管道定义(与线上 .gitlab-ci.yml 分离)。
 * 支持切换、新建与就地编辑;保存写回项目目录后自动刷新预览。
 */
function PipelineFileCard({ repositoryId }: { repositoryId: string }) {
  const repositories = useRepositories();
  const repository = repositories.data?.repositories.find((candidate) => candidate.id === repositoryId);
  const filesQuery = usePipelineFiles(repositoryId);
  const candidates = filesQuery.data?.files ?? [];
  const current = repository?.pipeline_file ?? filesQuery.data?.current ?? '.gitlab-ci.yml';
  const [editing, setEditing] = useState<{ path: string; exists: boolean } | null>(null);
  const [draft, setDraft] = useState('');
  const [customPath, setCustomPath] = useState('');
  const [showNew, setShowNew] = useState(false);
  const selectMutation = useSelectPipelineFile();
  const saveMutation = useSavePipelineFile();
  const contentQuery = usePipelineFileContent(repositoryId, editing?.exists ? editing.path : null);

  // 切换编辑目标时清空草稿;既有文件内容到达后回填
  useEffect(() => {
    setDraft('');
    if (editing?.exists && contentQuery.data) {
      setDraft(contentQuery.data.content);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editing?.path, contentQuery.data]);

  function openEditor(path: string, exists: boolean) {
    setEditing({ path, exists });
  }

  function handleNewOpen() {
    const path = customPath.trim();
    if (!path) return;
    setShowNew(false);
    setCustomPath('');
    openEditor(path, false);
  }

  function handleSave() {
    if (!editing) return;
    saveMutation.mutate(
      { repositoryId, payload: { path: editing.path, content: draft } },
      { onSuccess: () => setEditing(null) },
    );
  }

  const editorBusy = saveMutation.isPending || Boolean(contentQuery.isFetching && editing?.exists);

  return (
    <Card className="mb-3" data-testid="pipeline-file-card">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <FileCode2 className="size-4 text-primary" />
          管道文件
        </CardTitle>
        <CardDescription>
          仓库内任意 YAML 都可作为本地管道定义,与线上 .gitlab-ci.yml 分离;保存写回项目目录并立即刷新预览。
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <Badge variant="info">当前</Badge>
          <span className="min-w-0 truncate font-mono text-xs">{current}</span>
          <div className="ml-auto flex min-w-0 items-center gap-2">
            <Select value={current} onValueChange={(path) => selectMutation.mutate({ repositoryId, pipelineFile: path })}>
              <SelectTrigger className="w-56" aria-label="选择管道文件">
                <SelectValue placeholder="选择仓库内 YAML 文件" />
              </SelectTrigger>
              <SelectContent>
                {candidates.length === 0 && (
                  <div className="px-2 py-1.5 text-[11px] text-muted-foreground">仓库内暂无 .yml/.yaml 文件,可新建</div>
                )}
                {candidates.map((path) => (
                  <SelectItem key={path} value={path}>
                    <span className="font-mono text-xs">{path}</span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button variant="secondary" size="sm" onClick={() => openEditor(current, candidates.includes(current))}>
              编辑
            </Button>
            <Button variant="outline" size="sm" onClick={() => setShowNew((value) => !value)}>
              新建文件
            </Button>
          </div>
        </div>

        {showNew && (
          <div className="flex flex-wrap items-center gap-2">
            <Input
              value={customPath}
              onChange={(event) => setCustomPath(event.target.value)}
              onKeyDown={(event) => event.key === 'Enter' && handleNewOpen()}
              placeholder="新文件相对路径,如 pipedeck-ci.yml"
              className="max-w-xs font-mono text-xs"
            />
            <Button size="sm" disabled={!customPath.trim()} onClick={handleNewOpen}>
              创建并编辑
            </Button>
            <span className="text-[11px] text-muted-foreground">
              将写入 {repository?.path ?? '项目目录'}/{customPath.trim() || '…'}
            </span>
          </div>
        )}

        {editing && (
          <div className="grid gap-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="min-w-0 truncate font-mono text-[11px] text-muted-foreground">
                编辑 {editing.path} · 保存后写回项目目录并设为当前管道文件
              </span>
              <div className="flex items-center gap-2">
                {saveMutation.isError && (
                  <span className="text-[11px] text-danger">{saveMutation.error.message}</span>
                )}
                <Button size="sm" disabled={editorBusy || !draft.trim()} onClick={handleSave}>
                  {saveMutation.isPending ? <BusyLabel>保存中</BusyLabel> : <Save />}
                  保存到项目
                </Button>
                <Button variant="ghost" size="sm" disabled={saveMutation.isPending} onClick={() => setEditing(null)}>
                  取消
                </Button>
              </div>
            </div>
            {contentQuery.isError && editing.exists && (
              <Alert variant="destructive">
                <AlertTitle>无法读取文件</AlertTitle>
                <AlertDescription>{contentQuery.error.message}</AlertDescription>
              </Alert>
            )}
            <Textarea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              rows={16}
              spellCheck={false}
              className="font-mono text-xs leading-relaxed"
              placeholder={
                editing.exists
                  ? '# 在此编辑管道定义…'
                  : '# 新建本地管道文件:只放本地可执行 job(如 check/build),\n# 保存后将作为该仓库的本地管道定义,与线上 .gitlab-ci.yml 互不影响。'
              }
            />
          </div>
        )}
      </CardContent>
    </Card>
  );
}

const DECLARATION_PATH = '.pipedeck.yml';
const DECLARATION_TEMPLATE = `# Pipedeck 本地部署声明
# 声明中间件依赖、环境变量、端口、健康检查与异步 job;
# 缺失的结构由平台按项目类型补全,开发只写差异(与线上 .gitlab-ci.yml 同构)。
services: []
environment:
  # KEY: value
port: 8000
health: /health
jobs:
  # - name: my-job
  #   image: python:3.12-slim
  #   script: python -m app.scripts.my_job
`;

/**
 * 本地部署声明卡片:查看/编辑/保存项目的 `.pipedeck.yml`。
 * 声明驱动导入后的默认部署配置(绑定/环境变量/端口/异步 job),部署计划自动应用。
 */
function DeclarationCard({ repositoryId }: { repositoryId: string }) {
  const repositories = useRepositories();
  const repository = repositories.data?.repositories.find((candidate) => candidate.id === repositoryId);
  const filesQuery = usePipelineFiles(repositoryId);
  const exists = filesQuery.data?.files.includes(DECLARATION_PATH) ?? false;
  const contentQuery = usePipelineFileContent(repositoryId, exists ? DECLARATION_PATH : null);
  const saveMutation = useSavePipelineFile();
  const [draft, setDraft] = useState('');
  const [editing, setEditing] = useState(false);

  useEffect(() => {
    if (contentQuery.data) setDraft(contentQuery.data.content);
  }, [contentQuery.data]);

  function openCreate() {
    setDraft(DECLARATION_TEMPLATE);
    setEditing(true);
  }

  function handleSave() {
    saveMutation.mutate(
      { repositoryId, payload: { path: DECLARATION_PATH, content: draft } },
      {
        onSuccess: () => {
          setEditing(false);
          void queryClient.invalidateQueries({ queryKey: ['repositories'] });
        },
      },
    );
  }

  const queryClient = useQueryClient();
  const saveBusy = saveMutation.isPending || Boolean(contentQuery.isFetching);

  return (
    <Card className="mb-3" data-testid="declaration-card">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <FileCog className="size-4 text-primary" />
          本地部署声明
        </CardTitle>
        <CardDescription>
          声明中间件依赖、环境变量、端口、健康检查与异步 job;部署计划自动应用。敏感值请在 GUI 用 Secret/Host env 配置。
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <Badge variant={exists ? 'info' : 'outline'}>{exists ? '已存在' : '未创建'}</Badge>
          <span className="min-w-0 truncate font-mono text-xs">{DECLARATION_PATH}</span>
          {repository && (
            <span className="min-w-0 truncate text-[11px] text-muted-foreground">
              {repository.path}\{DECLARATION_PATH}
            </span>
          )}
          <div className="ml-auto flex items-center gap-2">
            {exists && (
              <Button variant="secondary" size="sm" onClick={() => setEditing(true)}>
                编辑
              </Button>
            )}
            <Button variant="outline" size="sm" onClick={openCreate}>
              <Plus />
              新建
            </Button>
          </div>
        </div>

        {contentQuery.isError && !contentQuery.error.message.includes('404') && (
          <Alert variant="destructive">
            <AlertTitle>无法读取声明</AlertTitle>
            <AlertDescription>{contentQuery.error.message}</AlertDescription>
          </Alert>
        )}

        {editing && (
          <div className="grid gap-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="min-w-0 truncate font-mono text-[11px] text-muted-foreground">
                编辑 {DECLARATION_PATH} · 保存后写回项目目录,刷新工作区/部署计划生效
              </span>
              <div className="flex items-center gap-2">
                {saveMutation.isError && <span className="text-[11px] text-danger">{saveMutation.error.message}</span>}
                <Button size="sm" disabled={saveBusy || !draft.trim()} onClick={handleSave}>
                  {saveMutation.isPending ? <BusyLabel>保存中</BusyLabel> : <Save />}
                  保存到项目
                </Button>
                <Button variant="ghost" size="sm" disabled={saveMutation.isPending} onClick={() => setEditing(false)}>
                  取消
                </Button>
              </div>
            </div>
            <Textarea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              rows={14}
              spellCheck={false}
              className="font-mono text-xs leading-relaxed"
              placeholder="# 在此编辑 .pipedeck.yml…"
            />
          </div>
        )}
      </CardContent>
    </Card>
  );
}
