import * as React from 'react';
import { Check } from 'lucide-react';

import { cn } from '@/lib/utils';

/**
 * 原生 input checkbox(样式化)。
 * 刻意不用 Radix Checkbox:清理预览等场景依赖原生 input语义(disabled 属性、check()/click() 可编程交互)。
 */
function Checkbox({
  className,
  checked,
  onCheckedChange,
  ...props
}: Omit<React.ComponentProps<'input'>, 'onChange' | 'checked'> & {
  checked?: boolean;
  onCheckedChange?: (checked: boolean) => void;
}) {
  return (
    <span className="relative inline-flex size-4 shrink-0">
      <input
        type="checkbox"
        data-slot="checkbox"
        className={cn(
          'peer size-4 appearance-none rounded-xs border border-[#46524a] bg-[#0b0e0c] shadow-xs outline-none',
          'checked:border-primary checked:bg-primary',
          'focus-visible:outline-2 focus-visible:outline-ring disabled:cursor-not-allowed disabled:opacity-50',
          className,
        )}
        checked={checked}
        onChange={(event) => onCheckedChange?.(event.target.checked)}
        {...props}
      />
      <Check className={cn('pointer-events-none absolute inset-0 m-auto size-3 text-primary-foreground', !checked && 'invisible')} />
    </span>
  );
}

export { Checkbox };
