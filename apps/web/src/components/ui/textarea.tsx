import * as React from 'react';

import { cn } from '@/lib/utils';

function Textarea({ className, ...props }: React.ComponentProps<'textarea'>) {
  return (
    <textarea
      data-slot="textarea"
      className={cn(
        'flex min-h-16 w-full rounded-sm border border-input bg-[#0b0e0c] px-2.5 py-1.5 text-xs',
        'placeholder:text-[#717c75] focus-visible:border-[#4d8290] focus-visible:outline-2 focus-visible:outline-ring/40',
        'disabled:cursor-not-allowed disabled:opacity-60',
        className,
      )}
      {...props}
    />
  );
}

export { Textarea };
