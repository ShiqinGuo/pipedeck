import type { ComponentType, ReactNode } from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';

import { ApiError } from '@/api/client';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';

/** 加载中标签 */
export function BusyLabel({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <RefreshCw className="size-3.5 is-spinning" />
      {children}
    </span>
  );
}

/** 空态:给"下一步做什么" */
export function EmptyState({
  icon: Icon,
  title,
  detail,
  action,
}: {
  icon?: ComponentType<{ className?: string }>;
  title: string;
  detail: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-md border border-dashed border-border px-4 py-8 text-center">
      {Icon && <Icon className="size-7 text-[#6d7771]" />}
      <p className="text-xs font-semibold text-foreground">{title}</p>
      <p className="max-w-sm text-[11px] leading-relaxed text-muted-foreground">{detail}</p>
      {action}
    </div>
  );
}

/** 错误态:recovery 提示 + 重试路径 */
export function ErrorState({
  error,
  onRetry,
  title,
}: {
  error: unknown;
  onRetry?: () => void;
  title: string;
}) {
  const recovery = error instanceof ApiError ? error.recovery : null;
  const detail = error instanceof Error ? error.message : String(error);
  return (
    <div className="rounded-md border border-[#754246] bg-danger-soft px-3 py-2.5 text-xs" role="alert">
      <div className="flex items-center gap-2">
        <AlertTriangle className="size-4 shrink-0 text-danger" />
        <span className="font-semibold text-danger">{title}</span>
      </div>
      <p className="mt-1 pl-6 leading-relaxed text-danger/90">{detail}</p>
      <div className="mt-1.5 flex flex-wrap items-center gap-2 pl-6">
        {recovery && <span className="text-[11px] text-warn">恢复方式:{recovery}</span>}
        {onRetry && (
          <Button variant="secondary" size="sm" onClick={onRetry}>
            <RefreshCw />重试
          </Button>
        )}
      </div>
    </div>
  );
}

/** 变更失败提示(mutation error),带 token 鉴权失败原因与恢复路径 */
export function MutationError({ error }: { error: unknown }) {
  if (!error) return null;
  return <ErrorState error={error} title="操作未完成" />;
}

/** 列表骨架:首载骨架屏 */
export function ListSkeleton({ rows = 4, className }: { rows?: number; className?: string }) {
  return (
    <div className={cn('flex flex-col gap-2', className)} aria-busy="true" aria-label="正在加载">
      {Array.from({ length: rows }, (_, index) => (
        <Skeleton key={index} className="h-12 w-full" />
      ))}
    </div>
  );
}

/** 卡片骨架 */
export function CardSkeleton({ className }: { className?: string }) {
  return <Skeleton className={cn('h-32 w-full', className)} />;
}

/** 禁用按钮的 reason 徽标(title + aria-label 双通道) */
export function DisabledReason({ reason }: { reason: string | null }) {
  if (!reason) return null;
  return (
    <Badge variant="warn" className="whitespace-normal">
      {reason}
    </Badge>
  );
}

/** 状态点 */
export function StatusDot({ active, className }: { active?: boolean; className?: string }) {
  return (
    <span
      className={cn(
        'size-1.5 shrink-0 rounded-full',
        active ? 'bg-ok shadow-[0_0_0_3px_rgba(96,215,160,0.12)]' : 'bg-[#6d7771] shadow-[0_0_0_3px_rgba(109,119,113,0.11)]',
        className,
      )}
    />
  );
}
