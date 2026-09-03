import { Link } from '@tanstack/react-router';
import { ArrowRightLeft, Download, FolderInput, GitBranch, GitCommitHorizontal, Play, RefreshCw } from 'lucide-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import type { components } from '@/api/schema';
import { useApiToken, useRepositories } from '@/api/hooks';
import { CliFooter } from '@/components/cli-command';
import { PageBody, PageHeader, PageScroll } from '@/components/page';
import { Badge } from '@/components/ui/badge';
import { BusyLabel, EmptyState, ErrorState, ListSkeleton, MutationError } from '@/components/states';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { api } from '@/api/client';
import { BROWSER_DIRECTORY_PICKER_REASON, pickNativeDirectory } from '@/lib/directory-picker';
import { cli } from '@/lib/cli';
import { truncateMiddle } from '@/lib/utils';

type RepositoryRecord = components['schemas']['RepositoryRecord'];

type DialogMode = 'closed' | 'import' | 'clone' | { kind: 'checkout'; repository: RepositoryRecord };

function RepositoryRow({
  repository,
  onOpenDialog,
  tokenReady,
}: {
  repository: RepositoryRecord;
  onOpenDialog: (mode: DialogMode) => void;
  tokenReady: boolean;
}) {
  const queryClient = useQueryClient();
  const update = useMutation({
    mutationFn: () => api.updateRepository(repository.id),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['repositories'] }),
  });
  const updateDisabledReason = !tokenReady
    ? '写操作需要本地写入令牌'
    : repository.dirty
      ? 'worktree 有未提交修改,更新会以 fast-forward 失败;请先提交或暂存'
      : update.isPending
        ? '正在更新'
        : null;
  return (
    <li className="grid gap-x-4 gap-y-2 border-b border-[#252c28] px-3 py-3 md:grid-cols-[minmax(0,1fr)_auto]" data-testid="repository-row">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className="truncate text-xs font-semibold">{repository.name}</span>
          {repository.dirty ? <Badge variant="warn">DIRTY</Badge> : <Badge variant="ok">CLEAN</Badge>}
        </div>
        <p className="mt-1 flex min-w-0 flex-wrap items-center gap-x-3 gap-y-0.5 font-mono text-[11px] text-muted-foreground">
          <span className="truncate" title={repository.path}>
            {repository.path}
          </span>
          <span className="truncate">
            <GitBranch className="mr-1 inline size-3" />
            {repository.branch}
          </span>
          <span className="truncate" title={repository.head_sha}>
            <GitCommitHorizontal className="mr-1 inline size-3" />
            {truncateMiddle(repository.head_sha, 12)}
          </span>
          {repository.origin_url && <span className="truncate">{repository.origin_url}</span>}
        </p>
        {update.isError && (
          <div className="mt-2">
            <MutationError error={update.error} />
          </div>
        )}
      </div>
      <div className="flex flex-wrap items-start gap-2">
        <Button
          variant="secondary"
          size="sm"
          disabled={Boolean(updateDisabledReason)}
          title={updateDisabledReason ?? `更新 ${repository.name}`}
          aria-label={`更新 ${repository.name}`}
          onClick={() => update.mutate()}
        >
          {update.isPending ? <BusyLabel>更新中</BusyLabel> : <RefreshCw />}更新
        </Button>
        <Button variant="secondary" size="sm" onClick={() => onOpenDialog({ kind: 'checkout', repository })}>
          <ArrowRightLeft />切换 ref
        </Button>
        <Button asChild variant="default" size="sm">
          <Link to="/pipelines/$repoId" params={{ repoId: repository.id }} aria-label={`打开 ${repository.name} 管道预览`}>
            <Play />
            管道
          </Link>
        </Button>
      </div>
    </li>
  );
}

function ImportDialog({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const [path, setPath] = useState('');
  const [pickerBusy, setPickerBusy] = useState(false);
  const nativePicker = typeof window !== 'undefined' && Boolean((window as Window & { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__);
  const importMutation = useMutation({
    mutationFn: (payload: { path: string }) => api.importRepository(payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['repositories'] });
      void queryClient.invalidateQueries({ queryKey: ['overview'] });
      onClose();
    },
  });
  const browse = async () => {
    setPickerBusy(true);
    try {
      const picked = await pickNativeDirectory({ title: '导入本机仓库' });
      if (picked) setPath(picked);
    } catch (error) {
      // 浏览器开发模式:保持手工输入路径
      void error;
    } finally {
      setPickerBusy(false);
    }
  };
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent aria-describedby={undefined}>
        <DialogHeader eyebrow="REPOSITORIES" title="导入本机仓库" />
        <DialogBody>
          <div className="grid gap-2">
            <div className="grid gap-1.5">
              <Label htmlFor="import-path">本机目录</Label>
              <div className="flex flex-wrap gap-2">
                <Input id="import-path" value={path} onChange={(event) => setPath(event.target.value)} placeholder="D:\code\repository" className="min-w-0 flex-1" />
                <Button
                  variant="secondary"
                  size="sm"
                  type="button"
                  onClick={() => void browse()}
                  disabled={!nativePicker || pickerBusy}
                  title={nativePicker ? '打开系统目录选择器' : BROWSER_DIRECTORY_PICKER_REASON}
                >
                  选择目录
                </Button>
              </div>
              {!nativePicker && <p className="text-[11px] leading-relaxed text-warn">{BROWSER_DIRECTORY_PICKER_REASON}</p>}
            </div>
            <MutationError error={importMutation.error} />
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="secondary" onClick={onClose}>
            取消
          </Button>
          <Button
            disabled={importMutation.isPending || !path.trim()}
            title={!path.trim() ? '请填写本机目录' : '导入仓库'}
            onClick={() => importMutation.mutate({ path: path.trim() })}
          >
            {importMutation.isPending ? <BusyLabel>导入中</BusyLabel> : <FolderInput />}
            导入仓库
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function CloneDialog({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const [url, setUrl] = useState('');
  const [destinationParent, setDestinationParent] = useState('');
  const [directoryName, setDirectoryName] = useState('');
  const [pickerBusy, setPickerBusy] = useState(false);
  const nativePicker = typeof window !== 'undefined' && Boolean((window as Window & { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__);
  const cloneMutation = useMutation({
    mutationFn: (payload: { url: string; destination_parent: string; directory_name: string | null }) => api.cloneRepository(payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['repositories'] });
      void queryClient.invalidateQueries({ queryKey: ['overview'] });
      onClose();
    },
  });
  const browse = async () => {
    setPickerBusy(true);
    try {
      const picked = await pickNativeDirectory({ title: '选择克隆目标父目录' });
      if (picked) setDestinationParent(picked);
    } catch {
      // 浏览器开发模式:手工输入
    } finally {
      setPickerBusy(false);
    }
  };
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent aria-describedby={undefined}>
        <DialogHeader eyebrow="REPOSITORIES" title="克隆仓库" />
        <DialogBody>
          <div className="grid gap-2">
            <div className="grid gap-1.5">
              <Label htmlFor="clone-url">Git URL</Label>
              <Input id="clone-url" value={url} onChange={(event) => setUrl(event.target.value)} placeholder="git@gitlab.example.com:pipedeck/demo.git" />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="clone-destination">目标父目录</Label>
              <div className="flex flex-wrap gap-2">
                <Input id="clone-destination" value={destinationParent} onChange={(event) => setDestinationParent(event.target.value)} placeholder="D:\code" className="min-w-0 flex-1" />
                <Button
                  variant="secondary"
                  size="sm"
                  type="button"
                  onClick={() => void browse()}
                  disabled={!nativePicker || pickerBusy}
                  title={nativePicker ? '打开系统目录选择器' : BROWSER_DIRECTORY_PICKER_REASON}
                >
                  选择目录
                </Button>
              </div>
              {!nativePicker && <p className="text-[11px] leading-relaxed text-warn">{BROWSER_DIRECTORY_PICKER_REASON}</p>}
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="clone-directory">目录名（可选）</Label>
              <Input id="clone-directory" value={directoryName} onChange={(event) => setDirectoryName(event.target.value)} placeholder="demo" />
            </div>
            <MutationError error={cloneMutation.error} />
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="secondary" onClick={onClose}>
            取消
          </Button>
          <Button
            disabled={cloneMutation.isPending || !url.trim() || !destinationParent.trim()}
            title={!url.trim() || !destinationParent.trim() ? 'Git URL 与目标父目录不能为空' : '开始克隆'}
            onClick={() =>
              cloneMutation.mutate({
                url: url.trim(),
                destination_parent: destinationParent.trim(),
                directory_name: directoryName.trim() || null,
              })
            }
          >
            {cloneMutation.isPending ? <BusyLabel>克隆中</BusyLabel> : <Download />}
            开始克隆
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function CheckoutDialog({ repository, onClose }: { repository: RepositoryRecord; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [ref, setRef] = useState(repository.branch);
  const checkoutMutation = useMutation({
    mutationFn: (payload: { ref: string }) => api.checkoutRepository(repository.id, payload),
    onSuccess: (updated) => {
      // checkout 响应即新仓库状态,直接写入缓存;refetch 旧列表会把 HEAD 回滚显示
      queryClient.setQueryData<components['schemas']['RepositoryListResponse']>(['repositories'], (current) =>
        current
          ? { repositories: current.repositories.map((item) => (item.id === updated.id ? updated : item)) }
          : current,
      );
      void queryClient.invalidateQueries({ queryKey: ['pipeline-preview'] });
      onClose();
    },
  });
  const blocked = checkoutMutation.isError;
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent aria-describedby={undefined}>
        <DialogHeader eyebrow="DESTRUCTIVE-ADJACENT" title={`切换 ref · ${repository.name}`} />
        <DialogBody>
          <div className="grid gap-2">
            <p className="text-[11px] leading-relaxed text-muted-foreground">
              更新 ref 会改变 checkout 的 HEAD。dirty worktree 不重置不覆盖;更新默认 fast-forward only。
            </p>
            <div className="grid gap-1.5">
              <Label htmlFor="checkout-ref">目标 ref(branch / tag / SHA)</Label>
              <Input id="checkout-ref" value={ref} onChange={(event) => setRef(event.target.value)} placeholder="main" />
            </div>
            {blocked && <MutationError error={checkoutMutation.error} />}
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="secondary" onClick={onClose}>
            取消
          </Button>
          <Button
            variant={blocked ? 'secondary' : 'default'}
            disabled={checkoutMutation.isPending || !ref.trim()}
            title={!ref.trim() ? '请填写目标 ref' : blocked ? '修正 ref 或冲突后重试' : `切换到 ${ref.trim()}`}
            onClick={() => checkoutMutation.mutate({ ref: ref.trim() })}
          >
            {checkoutMutation.isPending ? <BusyLabel>切换中</BusyLabel> : <ArrowRightLeft />}
            {blocked ? '重试切换' : '切换 ref'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function ProjectsRoute() {
  const repositories = useRepositories();
  const token = useApiTokenIfReady();
  const [dialog, setDialog] = useState<DialogMode>('closed');
  const records = repositories.data?.repositories ?? [];
  return (
    <PageScroll>
      <PageHeader
        eyebrow="PROJECTS"
        title="仓库"
        description="扫描/导入/克隆本机仓库;每行展示分支与 HEAD 状态。无 .gitlab-ci.yml 的仓库仅有扫描/部署能力。"
        actions={
          <>
            <Button variant="secondary" size="sm" onClick={() => setDialog('import')} title={token.reason ?? '导入本机仓库'} disabled={token.disabled}>
              <FolderInput />导入
            </Button>
            <Button variant="secondary" size="sm" onClick={() => setDialog('clone')} title={token.reason ?? '克隆仓库'} disabled={token.disabled}>
              <Download />克隆仓库
            </Button>
          </>
        }
      />
      <PageBody>
        {repositories.isLoading ? (
          <ListSkeleton rows={4} />
        ) : repositories.isError ? (
          <ErrorState error={repositories.error} onRetry={() => void repositories.refetch()} title="无法读取仓库列表" />
        ) : records.length === 0 ? (
          <EmptyState
            icon={GitBranch}
            title="还没有导入仓库"
            detail="导入本机已有 checkout,或从 GitLab 克隆;导入后即可预览 .gitlab-ci.yml 管道"
            action={
              <div className="mt-1 flex flex-wrap justify-center gap-2">
                <Button size="sm" onClick={() => setDialog('import')} disabled={token.disabled} title={token.reason ?? '导入本机仓库'}>
                  <FolderInput />导入仓库
                </Button>
                <Button variant="secondary" size="sm" onClick={() => setDialog('clone')} disabled={token.disabled} title={token.reason ?? '克隆仓库'}>
                  <Download />克隆仓库
                </Button>
              </div>
            }
          />
        ) : (
          <ul className="overflow-hidden rounded-md border border-border bg-card">
            {records.map((repository) => (
              <RepositoryRow key={repository.id} repository={repository} onOpenDialog={setDialog} tokenReady={token.ready} />
            ))}
          </ul>
        )}
        <CliFooter command={cli.reposList()} hint="等价 CLI:仓库列表" />
      </PageBody>

      {dialog === 'import' && <ImportDialog onClose={() => setDialog('closed')} />}
      {dialog === 'clone' && <CloneDialog onClose={() => setDialog('closed')} />}
      {typeof dialog === 'object' && dialog.kind === 'checkout' && (
        <CheckoutDialog repository={dialog.repository} onClose={() => setDialog('closed')} />
      )}
    </PageScroll>
  );
}

/** 写操作按钮依赖 token;只读模式下禁用并给 reason */
function useApiTokenIfReady() {
  const token = useApiToken();
  return {
    ready: token.configured,
    disabled: !token.configured,
    reason: token.disabledReason,
  };
}
