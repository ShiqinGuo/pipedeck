/* eslint-disable react-refresh/only-export-components */
import { AlertCircle, Braces, CheckCircle2, LoaderCircle, LockKeyhole, TriangleAlert, X } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';

import { ApiError } from './api';
import type { MiddlewareKind, ProjectKind, RunStatus } from './types';
import { useApiToken } from './use-api-token';

export const KIND_LABELS: Record<ProjectKind, string> = {
  'python-uv': 'Python / uv',
  'vite-react': 'React / Vite',
  'vite-vue': 'Vue / Vite',
  nuxt: 'Nuxt',
  compose: 'Compose',
  unknown: '待配置',
};

export const MIDDLEWARE_LABELS: Record<MiddlewareKind, string> = {
  postgres: 'PostgreSQL',
  redis: 'Redis',
  elasticsearch: 'Elasticsearch',
  minio: 'MinIO',
};

export const RUN_STATUS_LABELS: Record<RunStatus, string> = {
  queued: '排队中',
  running: '运行中',
  succeeded: '已通过',
  failed: '失败',
  cancelled: '已取消',
  interrupted: '已中断',
};

const PROJECT_LOGOS: Record<ProjectKind, string> = {
  'python-uv': 'python',
  'vite-react': 'react',
  'vite-vue': 'vue',
  nuxt: 'nuxt',
  compose: 'docker',
  unknown: 'unknown',
};

export function TechLogo({ kind }: { kind: ProjectKind }) {
  const logo = PROJECT_LOGOS[kind];
  return logo === 'unknown'
    ? <Braces className="fallback-logo" size={18} aria-hidden="true" />
    : <span className={`tech-logo logo-${logo}`} aria-hidden="true" />;
}

export function MiddlewareLogo({ kind }: { kind: MiddlewareKind }) {
  return <span className={`tech-logo logo-${kind}`} aria-hidden="true" />;
}

export function StatusDot({ active }: { active: boolean }) {
  return <span className={active ? 'status-dot is-active' : 'status-dot'} aria-hidden="true" />;
}

export function StatusPill({ status }: { status: RunStatus }) {
  return <span className={`status-pill status-${status}`}><span />{RUN_STATUS_LABELS[status]}</span>;
}

export function formatDate(value: string | null) {
  if (!value) return '—';
  return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date(value));
}

export function formatDuration(startedAt: string | null, finishedAt: string | null) {
  if (!startedAt) return '未开始';
  const end = finishedAt ? new Date(finishedAt).getTime() : Date.now();
  const seconds = Math.max(0, Math.round((end - new Date(startedAt).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

export function getErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : '发生未知错误';
}

export function ErrorState({ error, onRetry, title = '无法读取本地状态' }: { error: unknown; onRetry?: () => void; title?: string }) {
  const apiError = error instanceof ApiError ? error : null;
  return (
    <div className="error-state" role="alert">
      <AlertCircle size={19} />
      <div><strong>{title}</strong><span>{getErrorMessage(error)}</span>{apiError?.recovery && <small>{apiError.recovery}</small>}</div>
      {onRetry && <button className="text-button" type="button" onClick={onRetry}>重试</button>}
    </div>
  );
}

export function EmptyState({ icon: Icon, title, detail, action }: { icon: LucideIcon; title: string; detail: string; action?: ReactNode }) {
  return <div className="empty-state"><Icon size={26} /><strong>{title}</strong><span>{detail}</span>{action}</div>;
}

export function LoadingRows({ count = 6 }: { count?: number }) {
  return <div className="loading-rows" aria-label="正在加载">{Array.from({ length: count }, (_, index) => <div key={index} />)}</div>;
}

export function MutationError({ error }: { error: unknown }) {
  const apiError = error instanceof ApiError ? error : null;
  return <div className="inline-message is-error" role="alert"><TriangleAlert size={16} /><span>{getErrorMessage(error)}{apiError?.recovery && <small>{apiError.recovery}</small>}</span></div>;
}

export function SuccessMessage({ children }: { children: ReactNode }) {
  return <div className="inline-message is-success" role="status"><CheckCircle2 size={16} /><span>{children}</span></div>;
}

export function TokenReason({ compact = false }: { compact?: boolean }) {
  const token = useApiToken();
  if (token.configured) return null;
  return <div className={compact ? 'token-reason is-compact' : 'token-reason'}><LockKeyhole size={15} /><span>{token.disabledReason}，当前为只读模式</span></div>;
}

export function BusyLabel({ children }: { children: ReactNode }) {
  return <><LoaderCircle className="is-spinning" size={16} />{children}</>;
}

export function Modal({ title, eyebrow, onClose, children, footer, wide = false }: { title: string; eyebrow?: string; onClose: () => void; children: ReactNode; footer?: ReactNode; wide?: boolean }) {
  const dialogRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const onCloseRef = useRef(onClose);

  useEffect(() => { onCloseRef.current = onClose; }, [onClose]);
  useEffect(() => {
    const returnFocusTarget = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); onCloseRef.current(); return; }
      if (event.key !== 'Tab' || !dialogRef.current) return;
      const items = Array.from(dialogRef.current.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'));
      const first = items[0];
      const last = items[items.length - 1];
      if (!first || !last) return;
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', handleKey);
    return () => { document.removeEventListener('keydown', handleKey); returnFocusTarget?.focus(); };
  }, []);

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.currentTarget === event.target) onClose(); }}>
      <section ref={dialogRef} className={wide ? 'modal-panel is-wide' : 'modal-panel'} role="dialog" aria-modal="true" aria-labelledby="modal-title">
        <header><div>{eyebrow && <span>{eyebrow}</span>}<h2 id="modal-title">{title}</h2></div><button ref={closeRef} className="icon-button" type="button" aria-label="关闭" title="关闭" onClick={onClose}><X size={18} /></button></header>
        <div className="modal-body">{children}</div>
        {footer && <footer>{footer}</footer>}
      </section>
    </div>
  );
}
