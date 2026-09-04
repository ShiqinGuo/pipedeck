import { useNavigate } from '@tanstack/react-router';
import { Play } from 'lucide-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import type { components } from '@/api/schema';
import { api } from '@/api/client';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Dialog, DialogBody, DialogContent, DialogFooter, DialogHeader } from '@/components/ui/dialog';
import { BusyLabel, ErrorState } from '@/components/states';
import { CliCommand } from '@/components/cli-command';

type WorkspacePlanResponse = components['schemas']['WorkspacePlanResponse'];
type PlanIssue = components['schemas']['PlanIssue'];

/** 阻断项分区:永不静默,醒目展示并给 recovery */
export function PlanIssueList({ issues, tone }: { issues: PlanIssue[]; tone: 'blocker' | 'warning' }) {
  const { t } = useTranslation();
  if (issues.length === 0) return null;
  const blocker = tone === 'blocker';
  return (
    <Alert variant={blocker ? 'destructive' : 'warning'} data-testid={blocker ? 'plan-blockers' : 'plan-warnings'}>
      <div className="min-w-0 flex-1">
        <AlertTitle>{blocker ? t('components.plan.issueList.blockers', { count: issues.length }) : t('components.plan.issueList.warnings', { count: issues.length })}</AlertTitle>
        <div className="mt-1.5 grid gap-1.5">
          {issues.map((issue) => (
            <div key={issue.code + issue.title}>
              <p className="font-semibold">
                {issue.title}
                <Badge variant="outline" className="ml-2 align-middle">
                  {issue.code}
                </Badge>
              </p>
              <AlertDescription>
                {issue.detail}
                {issue.recovery && <span className="block text-warn">{t('components.plan.recovery', { recovery: issue.recovery })}</span>}
              </AlertDescription>
            </div>
          ))}
        </div>
      </div>
    </Alert>
  );
}

/** 运行确认弹层:先看再跑——计划、命令、变量、immutable images,确认后才执行 */
export function PlanDialog({ plan, onClose }: { plan: WorkspacePlanResponse; onClose: () => void }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [runError, setRunError] = useState<unknown>(null);
  const runMutation = useMutation({
    mutationFn: (payload: components['schemas']['RunCreateRequest']) => api.createRun(payload),
    onSuccess: (run) => {
      void queryClient.invalidateQueries({ queryKey: ['runs'] });
      void queryClient.invalidateQueries({ queryKey: ['overview'] });
      onClose();
      void navigate({ to: '/runs/$runId', params: { runId: run.id } });
    },
    onError: setRunError,
  });
  const canRun = plan.ready && plan.plan_id !== null && !runMutation.isPending;
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent wide aria-describedby={undefined} data-testid="plan-dialog">
        <DialogHeader eyebrow="RUN PLAN" title={t('components.plan.dialog.title')} />
        <DialogBody>
          <div className="grid gap-3">
            <PlanIssueList issues={plan.blockers} tone="blocker" />
            <PlanIssueList issues={plan.warnings} tone="warning" />
            {plan.connection_mappings.map((mapping) => (
              <div key={mapping.project_id + mapping.kind} className="rounded-md border border-border px-3 py-2" data-testid="plan-connections">
                <p className="text-xs font-semibold">
                  {t('components.plan.connectionPreview', { resource: mapping.resource_name, kind: mapping.kind })}
                </p>
                <dl className="mt-1 grid gap-0.5 font-mono text-[11px]">
                  {mapping.outputs.map((output) => (
                    <div key={output.name} className="flex min-w-0 gap-2">
                      <dt className="shrink-0 font-semibold text-info">{output.name}</dt>
                      <dd className={output.sensitive ? 'truncate text-warn' : 'truncate text-muted-foreground'}>{output.redacted_value}</dd>
                    </div>
                  ))}
                </dl>
              </div>
            ))}
            {plan.steps.map((step) => (
              <section key={step.id} className="rounded-md border border-border">
                <header className="border-b border-border px-3 py-2">
                  <p className="text-xs font-semibold">{step.title}</p>
                  <p className="text-[11px] text-muted-foreground">{step.detail}</p>
                </header>
                <div className="grid gap-1.5 px-3 py-2">
                  {step.commands.map((command) => (
                    <div key={command.command_id + command.project_id} className="min-w-0">
                      <p className="truncate text-[11px] text-muted-foreground">
                        {command.project_name} · {command.label} · {command.cwd}
                      </p>
                      <code className="plan-argv mt-0.5 flex flex-wrap gap-1 font-mono text-[11px] text-info">
                        {command.argv.map((token, index) => (
                          <b key={index} className="rounded-xs bg-[#0b0e0c] px-1 py-0.5 font-medium">
                            {token}
                          </b>
                        ))}
                      </code>
                    </div>
                  ))}
                  {step.deployments.map((deployment) => (
                    <div key={deployment.revision_id} className="rounded-sm border border-border bg-surface-2 px-2 py-1.5 text-[11px]">
                      <p className="font-semibold text-foreground">{t('components.plan.deploymentCompose', { path: deployment.checkout_path })}</p>
                      <p className="mt-0.5 font-mono break-all text-muted-foreground">{t('components.plan.deploymentImages', { images: deployment.immutable_images.join(', ') })}</p>
                      <p className="mt-0.5 text-muted-foreground">{t('components.plan.deploymentServices', { services: deployment.services.join(', ') })}</p>
                    </div>
                  ))}
                  {step.commands.length === 0 && step.deployments.length === 0 && (
                    <p className="text-[11px] text-muted-foreground">{t('components.plan.noCommands')}</p>
                  )}
                </div>
              </section>
            ))}
            {runError ? <ErrorState error={runError} title={t('components.plan.runFailedToStart')} /> : null}
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="secondary" onClick={onClose}>
            {t('components.dialog.close')}
          </Button>
          <Button
            disabled={!canRun}
            title={!plan.ready ? t('components.plan.runDisabledBlockers') : plan.plan_id === null ? t('components.plan.runDisabledIdentity') : runMutation.isPending ? t('components.plan.runStarting') : t('components.plan.runCreate')}
            onClick={() => plan.plan_id && runMutation.mutate({ plan_id: plan.plan_id, idempotency_key: crypto.randomUUID() })}
          >
            {runMutation.isPending ? <BusyLabel>{t('components.plan.starting')}</BusyLabel> : <Play />}
            {t('components.plan.run')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** 面板页脚 CLI 展示助手 */
export function PlanCliHint({ children }: { children: string }) {
  return <CliCommand command={children} label="CLI" />;
}
