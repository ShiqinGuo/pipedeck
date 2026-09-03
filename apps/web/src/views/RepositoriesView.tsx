import { Download, FolderInput, FolderOpen, GitBranch, GitPullRequestArrow, Plus, RefreshCw, Search } from 'lucide-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';

import { api } from '../api';
import { BROWSER_DIRECTORY_PICKER_REASON, hasNativeDirectoryPicker, pickNativeDirectory } from '../lib/directory-picker';
import { useApiToken } from '../use-api-token';
import { BusyLabel, EmptyState, ErrorState, Modal, MutationError, TokenReason, formatDate, getErrorMessage } from '../ui';

type RepositoryDialog = 'import' | 'clone' | null;

export function RepositoriesView() {
  const queryClient = useQueryClient();
  const token = useApiToken();
  const repositories = useQuery({ queryKey: ['repositories'], queryFn: api.repositories });
  const [query, setQuery] = useState('');
  const [dialog, setDialog] = useState<RepositoryDialog>(null);
  const [importPath, setImportPath] = useState('');
  const [cloneUrl, setCloneUrl] = useState('');
  const [cloneParent, setCloneParent] = useState('D:\\code');
  const [cloneDirectory, setCloneDirectory] = useState('');
  const [cloneBranch, setCloneBranch] = useState('');
  const [pickingDirectory, setPickingDirectory] = useState<RepositoryDialog>(null);
  const [directoryPickerError, setDirectoryPickerError] = useState<string | null>(null);
  const nativeDirectoryPicker = hasNativeDirectoryPicker();
  const directoryPickerReason = nativeDirectoryPicker ? null : BROWSER_DIRECTORY_PICKER_REASON;

  const openRepositoryDialog = (nextDialog: Exclude<RepositoryDialog, null>) => {
    setDirectoryPickerError(null);
    setDialog(nextDialog);
  };

  const chooseDirectory = async (target: Exclude<RepositoryDialog, null>) => {
    setDirectoryPickerError(null);
    setPickingDirectory(target);
    try {
      const selected = await pickNativeDirectory({
        title: target === 'import' ? '选择要导入的本机仓库' : '选择克隆目标父目录',
        currentPath: target === 'import' ? importPath : cloneParent,
      });
      if (!selected) return;
      if (target === 'import') setImportPath(selected);
      else setCloneParent(selected);
    } catch (error) {
      setDirectoryPickerError(`无法打开目录选择器：${getErrorMessage(error)}。请重试，或直接输入完整路径。`);
    } finally {
      setPickingDirectory(null);
    }
  };

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['repositories'] }),
      queryClient.invalidateQueries({ queryKey: ['catalog'] }),
      queryClient.invalidateQueries({ queryKey: ['overview'] }),
    ]);
  };

  const importMutation = useMutation({
    mutationFn: api.importRepository,
    onSuccess: async () => { setDialog(null); setImportPath(''); await refresh(); },
  });
  const cloneMutation = useMutation({
    mutationFn: api.cloneRepository,
    onSuccess: async () => { setDialog(null); setCloneUrl(''); setCloneDirectory(''); setCloneBranch(''); await refresh(); },
  });
  const updateMutation = useMutation({ mutationFn: api.updateRepository, onSuccess: refresh });

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return repositories.data?.repositories ?? [];
    return (repositories.data?.repositories ?? []).filter((repository) => [repository.name, repository.path, repository.origin_url, repository.branch].some((value) => value?.toLowerCase().includes(needle)));
  }, [query, repositories.data?.repositories]);

  return (
    <div className="page-scroll">
      <div className="page-heading compact-heading">
        <div><span>REPOSITORY CHECKOUTS</span><h1>仓库</h1><p>导入本机 checkout，或安全克隆并 fast-forward 更新</p></div>
        <div className="heading-actions"><button className="secondary-button" type="button" disabled={!token.configured} title={token.disabledReason ?? '导入本机目录'} onClick={() => openRepositoryDialog('import')}><FolderInput size={16} />导入</button><button className="primary-button" type="button" disabled={!token.configured} title={token.disabledReason ?? '克隆仓库'} onClick={() => openRepositoryDialog('clone')}><Plus size={16} />克隆仓库</button></div>
      </div>
      <TokenReason />
      <section className="table-toolbar"><div className="search-field"><Search size={16} /><input aria-label="搜索仓库" placeholder="搜索名称、路径、branch" value={query} onChange={(event) => setQuery(event.target.value)} /></div><span>{filtered.length} 个 checkout</span></section>
      {repositories.isError ? <ErrorState error={repositories.error} onRetry={() => void repositories.refetch()} title="无法读取仓库" /> : repositories.isLoading ? <div className="table-loading"><BusyLabel>正在读取仓库</BusyLabel></div> : filtered.length === 0 ? <EmptyState icon={GitPullRequestArrow} title={query ? '没有匹配的仓库' : '还没有接入仓库'} detail={query ? '调整搜索条件' : '导入现有 checkout 或克隆远端仓库'} /> : (
        <section className="data-table repository-table" aria-label="仓库列表">
          <header><span>仓库</span><span>Branch / HEAD</span><span>同步</span><span>更新时间</span><span /></header>
          {filtered.map((repository) => (
            <div className="table-row" key={repository.id}>
              <div className="repo-primary"><span className="row-icon"><GitPullRequestArrow size={17} /></span><span><strong>{repository.name}</strong><small title={repository.path}>{repository.path}</small><small title={repository.origin_url ?? undefined}>{repository.origin_url ?? '本机目录'}</small></span></div>
              <div className="repo-ref"><span><GitBranch size={13} />{repository.branch}</span><code>{repository.head_sha.slice(0, 9)}</code></div>
              <div>{repository.dirty ? <span className="state-label is-warning">有未提交修改</span> : repository.upstream ? <span className="state-label is-ready">可安全更新</span> : <span className="state-label">无 upstream</span>}</div>
              <time>{formatDate(repository.updated_at)}</time>
              <button className="icon-button" type="button" aria-label={`更新 ${repository.name}`} title={repository.dirty ? 'dirty worktree 不允许更新' : token.disabledReason ?? 'Fast-forward 更新'} disabled={repository.dirty || !repository.upstream || !token.configured || updateMutation.isPending} onClick={() => updateMutation.mutate(repository.id)}>{updateMutation.isPending && updateMutation.variables === repository.id ? <RefreshCw size={16} className="is-spinning" /> : <Download size={16} />}</button>
            </div>
          ))}
        </section>
      )}
      {updateMutation.isError && <MutationError error={updateMutation.error} />}

      {dialog === 'import' && <Modal title="导入本机仓库" eyebrow="LOCAL CHECKOUT" onClose={() => setDialog(null)} footer={<><button className="secondary-button" type="button" onClick={() => setDialog(null)}>取消</button><button className="primary-button" type="button" disabled={!token.configured || !importPath.trim() || importMutation.isPending} onClick={() => importMutation.mutate({ path: importPath.trim() })}>{importMutation.isPending ? <BusyLabel>正在导入</BusyLabel> : <><FolderInput size={16} />导入仓库</>}</button></>}>
        <div className="form-grid"><div className="field is-wide"><label htmlFor="repository-import-path">本机目录</label><span className="path-picker-control"><input id="repository-import-path" autoFocus value={importPath} onChange={(event) => setImportPath(event.target.value)} placeholder="D:\code\supplier-backend-v2" aria-describedby="repository-import-path-help" /><button className="secondary-button path-picker-button" type="button" disabled={!nativeDirectoryPicker || pickingDirectory !== null} title={directoryPickerReason ?? '打开系统目录选择器'} aria-describedby="repository-import-path-help" onClick={() => void chooseDirectory('import')}>{pickingDirectory === 'import' ? <BusyLabel>正在选择</BusyLabel> : <><FolderOpen size={16} />选择目录</>}</button></span><small id="repository-import-path-help" className={directoryPickerReason ? 'field-support is-warning' : 'field-support'}>{directoryPickerReason ?? '选择 Git checkout 根目录；也可以直接输入完整路径。'}</small></div></div>
        {directoryPickerError && <div className="inline-message is-error directory-picker-error" role="alert">{directoryPickerError}</div>}
        {importMutation.isError && <MutationError error={importMutation.error} />}
      </Modal>}

      {dialog === 'clone' && <Modal title="克隆仓库" eyebrow="REMOTE CHECKOUT" onClose={() => setDialog(null)} footer={<><button className="secondary-button" type="button" onClick={() => setDialog(null)}>取消</button><button className="primary-button" type="button" disabled={!token.configured || !cloneUrl.trim() || !cloneParent.trim() || cloneMutation.isPending} onClick={() => cloneMutation.mutate({ url: cloneUrl.trim(), destination_parent: cloneParent.trim(), directory_name: cloneDirectory.trim() || null, branch: cloneBranch.trim() || null })}>{cloneMutation.isPending ? <BusyLabel>正在克隆</BusyLabel> : <><Download size={16} />开始克隆</>}</button></>}>
        <div className="form-grid"><label className="field is-wide"><span>Git URL</span><input autoFocus value={cloneUrl} onChange={(event) => setCloneUrl(event.target.value)} placeholder="git@gitlab.example.com:pipedeck/project.git" /></label><div className="field is-wide"><label htmlFor="repository-clone-parent">目标父目录</label><span className="path-picker-control"><input id="repository-clone-parent" value={cloneParent} onChange={(event) => setCloneParent(event.target.value)} aria-describedby="repository-clone-parent-help" /><button className="secondary-button path-picker-button" type="button" disabled={!nativeDirectoryPicker || pickingDirectory !== null} title={directoryPickerReason ?? '打开系统目录选择器'} aria-describedby="repository-clone-parent-help" onClick={() => void chooseDirectory('clone')}>{pickingDirectory === 'clone' ? <BusyLabel>正在选择</BusyLabel> : <><FolderOpen size={16} />选择目录</>}</button></span><small id="repository-clone-parent-help" className={directoryPickerReason ? 'field-support is-warning' : 'field-support'}>{directoryPickerReason ?? '选择新 checkout 所在的父目录；也可以直接输入完整路径。'}</small></div><label className="field"><span>目录名（可选）</span><input value={cloneDirectory} onChange={(event) => setCloneDirectory(event.target.value)} /></label><label className="field"><span>Branch（可选）</span><input value={cloneBranch} onChange={(event) => setCloneBranch(event.target.value)} /></label></div>
        {directoryPickerError && <div className="inline-message is-error directory-picker-error" role="alert">{directoryPickerError}</div>}
        {cloneMutation.isError && <MutationError error={cloneMutation.error} />}
      </Modal>}
    </div>
  );
}
