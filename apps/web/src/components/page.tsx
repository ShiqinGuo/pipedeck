import type { ReactNode } from 'react';

import { cn } from '@/lib/utils';

/** 页面滚动容器 */
export function PageScroll({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn('page-scroll h-full overflow-y-auto px-4 py-6 md:px-7', className)}>{children}</div>;
}

/** 页头:eyebrow + 标题 + 描述 + 动作区 */
export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
  compact = false,
}: {
  eyebrow: string;
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
  compact?: boolean;
}) {
  return (
    <div className={cn('mx-auto mb-5 flex max-w-6xl flex-wrap items-start justify-between gap-x-6 gap-y-3', compact ? 'min-h-14' : 'min-h-18')}>
      <div className="min-w-0">
        <span className="mb-1.5 block font-mono text-[10px] font-medium tracking-wider text-primary uppercase">{eyebrow}</span>
        <h1 className="text-xl leading-tight font-semibold">{title}</h1>
        {description && <p className="mt-1.5 max-w-2xl text-xs leading-relaxed text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

/** 页面内容区(与 PageHeader 同宽对齐) */
export function PageBody({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn('mx-auto flex max-w-6xl flex-col gap-4', className)}>{children}</div>;
}
