import { Link } from '@tanstack/react-router';
import { ArrowRightLeft, Download, FolderInput, GitBranch, GitCommitHorizontal, Play, RefreshCw } from 'lucide-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

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
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const update = useMutation({
    mutationFn: () => api.updateRepository(repository.id),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['repositories'] }),
  });
  const updateDisabledReason = !tokenReady
    ? t('projects.repository.writeTokenRequired')
    : repository.dirty
      ? t('projects.repository.dirtyUpdate')
      : update.isPending
        ? t('projects.repository.updating')
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
          title={updateDisabledReason ?? t('projects.repository.updateTitle', { name: repository.name })}
          aria-label={t('projects.repository.updateTitle', { name: repository.name })}
          onClick={() => update.mutate()}
        >
          {update.isPending ? <BusyLabel>{t('projects.repository.updating')}</BusyLabel> : <RefreshCw />}{t('projects.repository.update')}
        </Button>
        <Button variant="secondary" size="sm" onClick={() => onOpenDialog({ kind: 'checkout', repository })}>
          <ArrowRightLeft />{t('projects.repository.switchRef')}
        </Button>
        <Button asChild variant="default" size="sm">
          <Link to="/pipelines/$repoId" params={{ repoId: repository.id }} aria-label={t('projects.repository.openPipeline', { name: repository.name })}>
            <Play />
            {t('projects.repository.pipeline')}
          </Link>
        </Button>
      </div>
    </li>
  );
}

function ImportDialog({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [path, setPath] = useState('');
  const [pickerBusy, setPickerBusy] = useState(false);
  const nativePicker = typeof window !== 'undefined' && Boolean((window as Window & { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__);
  const importMutation = useMutation({
    mutationFn: (payload: { path: string; pipeline_file?: string }) =>
      api.importRepository({ ...payload, pipeline_file: payload.pipeline_file ?? '.gitlab-ci.yml' }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['repositories'] });
      void queryClient.invalidateQueries({ queryKey: ['overview'] });
      onClose();
    },
  });
  const browse = async () => {
    setPickerBusy(true);
    try {
      const picked = await pickNativeDirectory({ title: t('projects.import.pickerTitle') });
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
        <DialogHeader eyebrow="REPOSITORIES" title={t('projects.import.title')} />
        <DialogBody>
          <div className="grid gap-2">
            <div className="grid gap-1.5">
              <Label htmlFor="import-path">{t('projects.import.localDir')}</Label>
              <div className="flex flex-wrap gap-2">
                <Input id="import-path" value={path} onChange={(event) => setPath(event.target.value)} placeholder="D:\code\repository" className="min-w-0 flex-1" />
                <Button
                  variant="secondary"
                  size="sm"
                  type="button"
                  onClick={() => void browse()}
                  disabled={!nativePicker || pickerBusy}
                  title={nativePicker ? t('projects.import.openPicker') : BROWSER_DIRECTORY_PICKER_REASON()}
                >
                  {t('projects.import.chooseDir')}
                </Button>
              </div>
              {!nativePicker && <p className="text-[11px] leading-relaxed text-warn">{BROWSER_DIRECTORY_PICKER_REASON()}</p>}
            </div>
            <MutationError error={importMutation.error} />
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="secondary" onClick={onClose}>
            {t('projects.import.cancel')}
          </Button>
          <Button
            disabled={importMutation.isPending || !path.trim()}
            title={!path.trim() ? t('projects.import.pathRequired') : t('projects.import.importRepo')}
            onClick={() => importMutation.mutate({ path: path.trim() })}
          >
            {importMutation.isPending ? <BusyLabel>{t('projects.import.importing')}</BusyLabel> : <FolderInput />}
            {t('projects.import.importRepo')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function CloneDialog({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [url, setUrl] = useState('');
  const [destinationParent, setDestinationParent] = useState('');
  const [directoryName, setDirectoryName] = useState('');
  const [pickerBusy, setPickerBusy] = useState(false);
  const nativePicker = typeof window !== 'undefined' && Boolean((window as Window & { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__);
  const cloneMutation = useMutation({
    mutationFn: (payload: { url: string; destination_parent: string; directory_name: string | null; pipeline_file?: string }) =>
      api.cloneRepository({ ...payload, pipeline_file: payload.pipeline_file ?? '.gitlab-ci.yml' }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['repositories'] });
      void queryClient.invalidateQueries({ queryKey: ['overview'] });
      onClose();
    },
  });
  const browse = async () => {
    setPickerBusy(true);
    try {
      const picked = await pickNativeDirectory({ title: t('projects.clone.pickerTitle') });
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
        <DialogHeader eyebrow="REPOSITORIES" title={t('projects.clone.title')} />
        <DialogBody>
          <div className="grid gap-2">
            <div className="grid gap-1.5">
              <Label htmlFor="clone-url">Git URL</Label>
              <Input id="clone-url" value={url} onChange={(event) => setUrl(event.target.value)} placeholder="git@gitlab.example.com:pipedeck/demo.git" />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="clone-destination">{t('projects.clone.destination')}</Label>
              <div className="flex flex-wrap gap-2">
                <Input id="clone-destination" value={destinationParent} onChange={(event) => setDestinationParent(event.target.value)} placeholder="D:\code" className="min-w-0 flex-1" />
                <Button
                  variant="secondary"
                  size="sm"
                  type="button"
                  onClick={() => void browse()}
                  disabled={!nativePicker || pickerBusy}
                  title={nativePicker ? t('projects.clone.openPicker') : BROWSER_DIRECTORY_PICKER_REASON()}
                >
                  {t('projects.clone.chooseDir')}
                </Button>
              </div>
              {!nativePicker && <p className="text-[11px] leading-relaxed text-warn">{BROWSER_DIRECTORY_PICKER_REASON()}</p>}
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="clone-directory">{t('projects.clone.directoryName')}</Label>
              <Input id="clone-directory" value={directoryName} onChange={(event) => setDirectoryName(event.target.value)} placeholder="demo" />
            </div>
            <MutationError error={cloneMutation.error} />
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="secondary" onClick={onClose}>
            {t('projects.clone.cancel')}
          </Button>
          <Button
            disabled={cloneMutation.isPending || !url.trim() || !destinationParent.trim()}
            title={!url.trim() || !destinationParent.trim() ? t('projects.clone.required') : t('projects.clone.start')}
            onClick={() =>
              cloneMutation.mutate({
                url: url.trim(),
                destination_parent: destinationParent.trim(),
                directory_name: directoryName.trim() || null,
              })
            }
          >
            {cloneMutation.isPending ? <BusyLabel>{t('projects.clone.cloning')}</BusyLabel> : <Download />}
            {t('projects.clone.start')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function CheckoutDialog({ repository, onClose }: { repository: RepositoryRecord; onClose: () => void }) {
  const { t } = useTranslation();
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
        <DialogHeader eyebrow="DESTRUCTIVE-ADJACENT" title={t('projects.checkout.title', { name: repository.name })} />
        <DialogBody>
          <div className="grid gap-2">
            <p className="text-[11px] leading-relaxed text-muted-foreground">
              {t('projects.checkout.description')}
            </p>
            <div className="grid gap-1.5">
              <Label htmlFor="checkout-ref">{t('projects.checkout.refLabel')}</Label>
              <Input id="checkout-ref" value={ref} onChange={(event) => setRef(event.target.value)} placeholder="main" />
            </div>
            {blocked && <MutationError error={checkoutMutation.error} />}
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="secondary" onClick={onClose}>
            {t('projects.checkout.cancel')}
          </Button>
          <Button
            variant={blocked ? 'secondary' : 'default'}
            disabled={checkoutMutation.isPending || !ref.trim()}
            title={!ref.trim() ? t('projects.checkout.refRequired') : blocked ? t('projects.checkout.fixAndRetry') : t('projects.checkout.switchTo', { ref: ref.trim() })}
            onClick={() => checkoutMutation.mutate({ ref: ref.trim() })}
          >
            {checkoutMutation.isPending ? <BusyLabel>{t('projects.checkout.switching')}</BusyLabel> : <ArrowRightLeft />}
            {blocked ? t('projects.checkout.retrySwitch') : t('projects.checkout.switchRef')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export default function ProjectsRoute() {
  const { t } = useTranslation();
  const repositories = useRepositories();
  const token = useApiTokenIfReady();
  const [dialog, setDialog] = useState<DialogMode>('closed');
  const records = repositories.data?.repositories ?? [];
  return (
    <PageScroll>
      <PageHeader
        eyebrow="PROJECTS"
        title={t('projects.title')}
        description={t('projects.description')}
        actions={
          <>
            <Button variant="secondary" size="sm" onClick={() => setDialog('import')} title={token.reason ?? t('projects.importRepo')} disabled={token.disabled}>
              <FolderInput />{t('projects.import')}
            </Button>
            <Button variant="secondary" size="sm" onClick={() => setDialog('clone')} title={token.reason ?? t('projects.cloneRepo')} disabled={token.disabled}>
              <Download />{t('projects.cloneRepo')}
            </Button>
          </>
        }
      />
      <PageBody>
        {repositories.isLoading ? (
          <ListSkeleton rows={4} />
        ) : repositories.isError ? (
          <ErrorState error={repositories.error} onRetry={() => void repositories.refetch()} title={t('projects.errorTitle')} />
        ) : records.length === 0 ? (
          <EmptyState
            icon={GitBranch}
            title={t('projects.empty.title')}
            detail={t('projects.empty.detail')}
            action={
              <div className="mt-1 flex flex-wrap justify-center gap-2">
                <Button size="sm" onClick={() => setDialog('import')} disabled={token.disabled} title={token.reason ?? t('projects.importRepo')}>
                  <FolderInput />{t('projects.importRepo')}
                </Button>
                <Button variant="secondary" size="sm" onClick={() => setDialog('clone')} disabled={token.disabled} title={token.reason ?? t('projects.cloneRepo')}>
                  <Download />{t('projects.cloneRepo')}
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
        <CliFooter command={cli.reposList()} hint={t('projects.cliHint')} />
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
