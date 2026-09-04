import { useTranslation } from 'react-i18next';

import type { components } from '@/api/schema';
import { Badge } from '@/components/ui/badge';
import { RUN_STATUS_LABELS, RUN_STATUS_TONES } from '@/lib/status';
import { cn } from '@/lib/utils';

type RunStatus = components['schemas']['RunStatus'];

/** Run 状态徽标(执行状态) */
export function RunStatusPill({ status, className }: { status: RunStatus; className?: string }) {
  const { t } = useTranslation();
  return (
    <Badge className={cn(RUN_STATUS_TONES[status], className)} data-testid={`run-status-${status}`}>
      {t(RUN_STATUS_LABELS[status])}
    </Badge>
  );
}
