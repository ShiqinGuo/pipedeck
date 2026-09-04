import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { CheckCircle2, FolderOpen, Languages, TerminalSquare, XCircle } from 'lucide-react';

import { useCatalog, useSession } from '@/api/hooks';
import { CliCommand } from '@/components/cli-command';
import { PageBody, PageHeader, PageScroll } from '@/components/page';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { BusyLabel, ErrorState } from '@/components/states';
import { setLanguage, SUPPORTED_LANGUAGES, type Language } from '@/i18n';
import { cli } from '@/lib/cli';
import { cn } from '@/lib/utils';

/** 界面语言切换 */
function LanguageCard() {
  const { t, i18n } = useTranslation();
  const current: Language = i18n.language === 'en' ? 'en' : 'zh';
  return (
    <Card data-testid="language-card">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Languages className="size-4 text-primary" />
          {t('settings.language.title')}
        </CardTitle>
        <CardDescription>{t('settings.language.description')}</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="grid gap-2">
          <span className="text-xs text-muted-foreground">{t('settings.language.current')}</span>
          <div className="flex flex-wrap gap-2">
            {SUPPORTED_LANGUAGES.map((language) => (
              <Button
                key={language}
                type="button"
                size="sm"
                variant={current === language ? 'default' : 'secondary'}
                className={cn(current !== language && 'text-muted-foreground')}
                onClick={() => setLanguage(language)}
              >
                {language === 'zh' ? '简体中文' : 'English'}
              </Button>
            ))}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

/** CLI 等价命令速查:桌面客户端安装后 CLI 即可用(安装器写 PATH / 应用内重装) */
function CliCard() {
  const { t } = useTranslation();
  const commands = [
    { command: cli.serve(), hint: t('settings.cli.serveHint') },
    { command: cli.status(), hint: t('settings.cli.statusHint') },
    { command: cli.reposList(), hint: t('settings.cli.reposListHint') },
    { command: 'pipedeck run <repo> --wait', hint: t('settings.cli.runHint') },
    { command: cli.doctor(), hint: t('settings.cli.doctorHint') },
  ];
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <TerminalSquare className="size-4 text-primary" />
          CLI
        </CardTitle>
        <CardDescription>{t('settings.cli.description')}</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        {commands.map((entry) => (
          <div key={entry.command} className="grid gap-1">
            <CliCommand command={entry.command} />
            <span className="pl-1 text-[11px] text-muted-foreground">{entry.hint}</span>
          </div>
        ))}
        <PathRepair />
      </CardContent>
    </Card>
  );
}

const TAURI_INTERNALS = '__TAURI_INTERNALS__';

/** PATH 修复:安装器已写一次;此按钮用于 PATH 被环境变量管理工具清掉后的手动恢复。 */
function PathRepair() {
  const { t } = useTranslation();
  const [status, setStatus] = useState<'idle' | 'ok' | 'error'>('idle');
  const nativePicker = typeof window !== 'undefined' && TAURI_INTERNALS in window;
  async function repair() {
    try {
      const { invoke } = await import('@tauri-apps/api/core');
      await invoke<string>('install_cli_to_path');
      setStatus('ok');
    } catch {
      setStatus('error');
    }
  }
  return (
    <div className="grid gap-1 rounded-sm border border-border bg-surface-2 p-2.5" data-testid="cli-path-repair">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs font-medium">{t('settings.path.title')}</span>
        <Button
          type="button"
          variant="secondary"
          size="sm"
          disabled={!nativePicker || status === 'ok'}
          title={nativePicker ? t('settings.path.repairTitle') : t('settings.path.browserUnavailable')}
          onClick={() => void repair()}
        >
          {t('settings.path.repairAction')}
        </Button>
      </div>
      <p className="text-[11px] leading-relaxed text-muted-foreground">
        {status === 'ok'
          ? t('settings.path.ok')
          : status === 'error'
            ? t('settings.path.error')
            : nativePicker
              ? t('settings.path.detail')
              : t('settings.path.browserUnavailableDetail')}
      </p>
    </div>
  );
}

function SessionCard() {
  const { t } = useTranslation();
  const session = useSession();
  return (
    <Card data-testid="session-card">
      <CardHeader>
        <CardTitle>{t('settings.session.title')}</CardTitle>
        <CardDescription>{t('settings.session.description')}</CardDescription>
      </CardHeader>
      <CardContent>
        {session.isLoading ? (
          <BusyLabel>{t('settings.session.loading')}</BusyLabel>
        ) : session.isError ? (
          <ErrorState error={session.error} onRetry={() => void session.refetch()} title={t('settings.session.errorTitle')} />
        ) : (
          <dl className="grid gap-1.5 text-xs">
            <div className="flex items-center gap-2">
              <dt className="text-muted-foreground">{t('settings.session.writeStatus')}</dt>
              <dd>
                {session.data?.write_enabled ? <Badge variant="ok">{t('settings.session.enabled')}</Badge> : <Badge variant="warn">{t('settings.session.readonly')}</Badge>}
              </dd>
            </div>
            <div className="flex items-center gap-2">
              <dt className="text-muted-foreground">{t('settings.session.authMethod')}</dt>
              <dd className="font-mono text-[11px]">{session.data?.authentication}</dd>
            </div>
          </dl>
        )}
      </CardContent>
    </Card>
  );
}

function ScanRootsCard() {
  const { t } = useTranslation();
  const catalog = useCatalog();
  return (
    <Card data-testid="scan-roots-card">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <FolderOpen className="size-4 text-primary" />
          {t('settings.scanRoots.title')}
        </CardTitle>
        <CardDescription>{t('settings.scanRoots.description')}</CardDescription>
      </CardHeader>
      <CardContent>
        {catalog.isLoading ? (
          <BusyLabel>{t('settings.scanRoots.loading')}</BusyLabel>
        ) : catalog.isError ? (
          <ErrorState error={catalog.error} onRetry={() => void catalog.refetch()} title={t('settings.scanRoots.errorTitle')} />
        ) : (
          <div className="grid gap-2 text-xs">
            <ul className="grid gap-1 font-mono text-[11px]">
              {(catalog.data?.roots ?? []).map((root) => (
                <li key={root}>{root}</li>
              ))}
            </ul>
            {(catalog.data?.errors.length ?? 0) === 0 ? (
              <p className="flex items-center gap-1.5 text-[11px] text-ok">
                <CheckCircle2 className="size-3.5" />
                {t('settings.scanRoots.allOk')}
              </p>
            ) : (
              <ul className="grid gap-1">
                {catalog.data?.errors.map((error) => (
                  <li key={error} className="flex items-start gap-1.5 text-[11px] text-warn">
                    <XCircle className="mt-0.5 size-3.5 shrink-0" />
                    {error}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export default function SettingsRoute() {
  const { t } = useTranslation();
  return (
    <PageScroll>
      <PageHeader eyebrow="SETTINGS" title={t('settings.page.title')} description={t('settings.page.description')} />
      <PageBody>
        <div className="grid gap-4 lg:grid-cols-2">
          <LanguageCard />
          <SessionCard />
          <ScanRootsCard />
        </div>
        <CliCard />
      </PageBody>
    </PageScroll>
  );
}
