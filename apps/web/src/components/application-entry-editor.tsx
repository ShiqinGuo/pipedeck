import { useTranslation } from 'react-i18next';
import type { components } from '@/api/schema';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

type Target = components['schemas']['HostTarget'] | components['schemas']['ComposeTarget'];

export function ApplicationEntryEditor({ target, onChange }: { target: Target; onChange: (target: Target) => void }) {
  const { t } = useTranslation();
  const endpoints = target.endpoints.filter((endpoint) => endpoint.protocol === 'tcp');
  const application = target.application;
  return (
    <section className="grid gap-2 rounded-md border border-border p-2.5">
      <div className="flex items-center gap-2">
        <Checkbox id="application-custom" checked={Boolean(application)} disabled={!endpoints.length && !application} onCheckedChange={(checked) => onChange({ ...target, application: checked ? { endpoint: endpoints[0]?.name ?? '', path: '/' } : null })} />
        <Label htmlFor="application-custom">{t('integration.application.custom')}</Label>
      </div>
      <p className="text-[11px] text-muted-foreground">{t(endpoints.length ? 'integration.application.hint' : 'integration.application.noEndpoint')}</p>
      {application && <div className="grid gap-2 sm:grid-cols-2">
        <div className="grid gap-1">
          <Label htmlFor="application-endpoint">{t('integration.application.endpoint')}</Label>
          <select id="application-endpoint" value={application.endpoint} onChange={(event) => onChange({ ...target, application: { ...application, endpoint: event.target.value } })} className="h-9 min-w-0 rounded-sm border border-input bg-surface px-2 text-xs">
            {!endpoints.some((endpoint) => endpoint.name === application.endpoint) && <option value={application.endpoint}>{t('integration.application.missingEndpoint')}</option>}
            {endpoints.map((endpoint) => <option key={endpoint.name} value={endpoint.name}>{endpoint.name} · {endpoint.host_port}</option>)}
          </select>
        </div>
        <div className="grid gap-1">
          <Label htmlFor="application-path">{t('integration.application.path')}</Label>
          <Input id="application-path" value={application.path} placeholder="/app" onChange={(event) => onChange({ ...target, application: { ...application, path: event.target.value } })} />
        </div>
      </div>}
    </section>
  );
}
