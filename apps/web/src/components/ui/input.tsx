import * as React from 'react';

import { cn } from '@/lib/utils';

function Input({ className, type, ...props }: React.ComponentProps<'input'>) {
  return (
    <input
      type={type}
      data-slot="input"
      className={cn(
        'flex h-9 min-w-0 rounded-sm border border-input bg-[#0b0e0c] px-2.5 py-1 text-xs text-foreground',
        'placeholder:text-[#717c75] focus-visible:border-[#4d8290] focus-visible:outline-2 focus-visible:outline-ring/40 focus-visible:outline-offset-0',
        'disabled:cursor-not-allowed disabled:opacity-60',
        className,
      )}
      {...props}
    />
  );
}

export { Input };
