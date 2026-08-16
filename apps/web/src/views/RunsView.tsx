import { Activity, Ban, Clock3, Copy, Download, Filter, ListRestart, PlayCircle, RefreshCw, Search, Square, TerminalSquare } from 'lucide-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';

import { api } from '../api';
import type { RunEventKind, RunRecord } from '../types';
import { BusyLabel, EmptyState, ErrorState, MutationError, RUN_STATUS_LABELS, StatusPill, TokenReason, formatDate, formatDuration } from '../ui';
import { useApiToken } from '../use-api-token';

const TERMINAL_STATUSES = new Set(['succeeded', 'failed', 'cancelled', 'interrupted']);

export function RunsView({ initialRunId, onSelectedRun }: { initialRunId: string | null; onSelectedRun: (runId: string) => void }) {
  const queryClient = useQueryClient();
  const token = useApiToken();
  const runs = useQuery({ queryKey: ['runs'], queryFn: api.runs, refetchInterval: 3000 });
  const [selectedId, setSelectedId] = useState<string | null>(initialRunId);
  const [logKind, setLogKind] = useState<'all' | RunEventKind>('all');
  const [logQuery, setLogQuery] = useState('');

  useEffect(() => { if (initialRunId) setSelectedId(initialRunId); }, [initialRunId]);
  useEffect(() => {
    const records = runs.data?.runs ?? [];
    if (!selectedId && records[0]) setSelectedId(records[0].id);
    if (selectedId && !records.some((run) => run.id === selectedId)) setSelectedId(records[0]?.id ?? null);
  }, [runs.data?.runs, selectedId]);

  const detail = useQuery({ queryKey: ['run', selectedId], queryFn: () => api.run(selectedId ?? ''), enabled: Boolean(selectedId), refetchInterval: (query) => {
    const run = query.state.data;
    return run && !TERMINAL_STATUSES.has(run.status) ? 1500 : false;
  } });
  const events = useQuery({ queryKey: ['run-events', selectedId], queryFn: () => api.runEvents(selectedId ?? '', 0), enabled: Boolean(selectedId), refetchInterval: detail.data && !TERMINAL_STATUSES.has(detail.data.status) ? 1200 : false });

  const refresh = async (runId?: string) => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['runs'] }),
      queryClient.invalidateQueries({ queryKey: ['run', runId ?? selectedId] }),
      queryClient.invalidateQueries({ queryKey: ['run-events', runId ?? selectedId] }),
      queryClient.invalidateQueries({ queryKey: ['overview'] }),
    ]);
  };
  const cancelMutation = useMutation({ mutationFn: api.cancelRun, onSuccess: (run) => refresh(run.id) });
  const retryMutation = useMutation({ mutationFn: api.retryRun, onSuccess: async (run) => { setSelectedId(run.id); onSelectedRun(run.id); await refresh(run.id); } });

  const selectedRun = detail.data ?? (runs.data?.runs ?? []).find((run) => run.id === selectedId) ?? null;
  const filteredEvents = useMemo(() => {
    const needle = logQuery.trim().toLowerCase();
    return (events.data?.events ?? []).filter((event) => (logKind === 'all' || event.kind === logKind) && (!needle || event.message.toLowerCase().includes(needle))).slice(-1000);
  }, [events.data?.events, logKind, logQuery]);
  const stageEvents = (events.data?.events ?? []).filter((event) => event.kind === 'stage');
  const canCancel = selectedRun?.status === 'queued' || selectedRun?.status === 'running';
  const canRetry = Boolean(selectedRun && TERMINAL_STATUSES.has(selectedRun.status));

  const selectRun = (runId: string) => { setSelectedId(runId); onSelectedRun(runId); setLogKind('all'); setLogQuery(''); };

  return (
    <div className="split-workbench runs-workbench">
      <aside className="context-sidebar">
        <header><div><span>RUN HISTORY</span><h1>运行记录</h1></div><button className="icon-button" type="button" aria-label="刷新运行记录" title="刷新" disabled={runs.isFetching} onClick={() => void runs.refetch()}><RefreshCw size={17} className={runs.isFetching ? 'is-spinning' : ''} /></button></header>
        <div className="run-filters"><button className="is-active" type="button">全部</button><span>{runs.data?.runs.length ?? 0}</span></div>
        <div className="context-list">
          {runs.isLoading ? <div className="sidebar-loading"><BusyLabel>正在读取</BusyLabel></div> : runs.isError ? <ErrorState error={runs.error} onRetry={() => void runs.refetch()} /> : (runs.data?.runs.length ?? 0) === 0 ? <EmptyState icon={Activity} title="还没有运行" detail="从工作区预检计划后启动" /> : runs.data?.runs.map((run) => <button className={selectedId === run.id ? 'context-row run-context-row is-active' : 'context-row run-context-row'} type="button" key={run.id} onClick={() => selectRun(run.id)}><span className="row-icon"><Activity size={17} /></span><span className="row-main"><strong>{run.workspace_name}</strong><small>{formatDate(run.created_at)} · {run.current_step ?? (run.mode === 'integrated' ? '集成' : '开发')}</small></span><StatusPill status={run.status} /></button>)}
        </div>
        <footer><TokenReason compact /></footer>
      </aside>

      <section className="run-main">
        {!selectedRun ? <EmptyState icon={PlayCircle} title="选择运行记录" detail="查看阶段、日志和恢复动作" /> : <>
          <header className="object-header run-header"><div className="object-title"><span className="run-glyph"><Activity size={19} /></span><div><h1>{selectedRun.workspace_name}</h1><p><code>{selectedRun.id.slice(0, 12)}</code> · revision {selectedRun.workspace_revision} · {selectedRun.mode === 'integrated' ? '集成模式' : '开发模式'}</p></div></div><div className="object-actions"><StatusPill status={selectedRun.status} /><button className="secondary-button" type="button" disabled={!canRetry || !token.configured || retryMutation.isPending} title={!canRetry ? '仅已结束运行可重试' : token.disabledReason ?? '重试运行'} onClick={() => retryMutation.mutate(selectedRun.id)}>{retryMutation.isPending ? <BusyLabel>重试中</BusyLabel> : <><ListRestart size={16} />重试</>}</button><button className="danger-button" type="button" disabled={!canCancel || !token.configured || cancelMutation.isPending} title={!canCancel ? '当前运行不可取消' : token.disabledReason ?? '取消运行'} onClick={() => cancelMutation.mutate(selectedRun.id)}>{cancelMutation.isPending ? <BusyLabel>取消中</BusyLabel> : <><Square size={15} fill="currentColor" />取消</>}</button></div></header>
          {(detail.isError || events.isError) && <ErrorState error={detail.error ?? events.error} onRetry={() => void refresh()} title="无法读取运行详情" />}
          {(cancelMutation.isError || retryMutation.isError) && <MutationError error={cancelMutation.error ?? retryMutation.error} />}
          <section className="run-facts"><span><Clock3 size={14} />{formatDuration(selectedRun.started_at, selectedRun.finished_at)}</span><span><TerminalSquare size={14} />{selectedRun.current_step ?? '等待阶段事件'}</span><span><Filter size={14} />{events.data?.events.length ?? 0} 条事件</span>{selectedRun.retry_of && <span><ListRestart size={14} />重试自 {selectedRun.retry_of.slice(0, 8)}</span>}</section>
          <div className="run-layout">
            <section className="run-timeline"><header><h2>执行阶段</h2><span>{RUN_STATUS_LABELS[selectedRun.status]}</span></header><div className="timeline-list">{stageEvents.length === 0 ? <EmptyState icon={Clock3} title="等待阶段事件" detail="控制服务开始执行后会显示阶段进度" /> : stageEvents.map((event, index) => <div className="timeline-row" key={event.sequence}><span className="timeline-index">{String(index + 1).padStart(2, '0')}</span><div><strong>{event.step_id ?? '运行阶段'}</strong><p>{event.message}</p><time>{formatDate(event.created_at)}</time></div></div>)}</div>{selectedRun.failure_detail && <div className="failure-panel"><Ban size={17} /><div><strong>{selectedRun.failure_code ?? 'RUN_FAILED'}</strong><span>{selectedRun.failure_detail}</span></div></div>}</section>
            <section className="log-console">
              <header><div><TerminalSquare size={16} /><h2>实时日志</h2><span className={canCancel ? 'live-indicator is-live' : 'live-indicator'}>{canCancel ? 'LIVE' : 'ARCHIVE'}</span></div><div className="log-actions"><button className="icon-button subtle" type="button" aria-label="复制可见日志" title="复制" onClick={() => void navigator.clipboard.writeText(filteredEvents.map((event) => event.message).join('\n'))}><Copy size={15} /></button><button className="icon-button subtle" type="button" aria-label="下载日志" title="下载" onClick={() => downloadLogs(selectedRun, filteredEvents.map((event) => `[${event.created_at}] ${event.kind} ${event.message}`).join('\n'))}><Download size={15} /></button></div></header>
              <div className="log-toolbar"><div className="search-field"><Search size={15} /><input aria-label="搜索日志" value={logQuery} onChange={(event) => setLogQuery(event.target.value)} placeholder="搜索日志" /></div><select aria-label="日志类型" value={logKind} onChange={(event) => setLogKind(event.target.value as 'all' | RunEventKind)}><option value="all">全部类型</option><option value="stdout">stdout</option><option value="stderr">stderr</option><option value="system">system</option><option value="status">status</option><option value="stage">stage</option></select></div>
              <div className="log-lines" aria-label="运行日志" aria-live="polite">{events.isLoading ? <BusyLabel>正在连接日志</BusyLabel> : filteredEvents.length === 0 ? <span className="log-empty">暂无匹配日志</span> : filteredEvents.map((event) => <div className={`log-line log-${event.kind}`} key={event.sequence}><time>{new Date(event.created_at).toLocaleTimeString('zh-CN', { hour12: false })}</time><span>{event.project_id?.slice(0, 12) ?? event.step_id ?? 'system'}</span><code>{event.message}</code></div>)}</div>
            </section>
          </div>
        </>}
      </section>
    </div>
  );
}

function downloadLogs(run: RunRecord, content: string) {
  const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `${run.workspace_name}-${run.id.slice(0, 8)}.log`;
  link.click();
  URL.revokeObjectURL(url);
}
