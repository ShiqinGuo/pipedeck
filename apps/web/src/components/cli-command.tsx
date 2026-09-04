import { useState } from 'react';
import { Check, Copy } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { cn } from '@/lib/utils';

/** 等价 CLI 命令展示:等宽 + 复制按钮。命令与实际执行严格同步。 */
export function CliCommand({ command, className, label }: { command: string; className?: string; label?: string }) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(command);
    } catch {
      // 剪贴板不可用(如非安全上下文)时退回选中文本,保持可用
      const selection = window.getSelection();
       
      console.warn('复制失败,请手动复制', selection !== null);
    }
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  };
  return (
    <span
      className={cn(
        'inline-flex min-w-0 max-w-full items-center gap-2 rounded-sm border border-border bg-[#0b0e0c] px-2 py-1',
        className,
      )}
    >
      {label && <span className="shrink-0 font-mono text-[10px] text-[#bfbfc3] uppercase">{label}</span>}
      <code className="min-w-0 flex-1 truncate font-mono text-[11px] text-info">{command}</code>
      <button
        type="button"
        aria-label={t('components.copy.commandArgs', { command })}
        title={copied ? t('components.copy.copied') : t('components.copy.command')}
        onClick={() => void copy()}
        className="shrink-0 rounded-xs p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
      >
        {copied ? <Check className="size-3.5 text-ok" /> : <Copy className="size-3.5" />}
      </button>
    </span>
  );
}

/** 操作面板页脚:一行等价命令 */
export function CliFooter({ command, hint }: { command: string; hint?: string }) {
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1.5 px-1 py-2">
      <CliCommand command={command} />
      {hint && <span className="text-[11px] text-[#bfbfc3]">{hint}</span>}
    </div>
  );
}
