import type { ReactNode } from 'react';
import { Link, useMatchRoute, useRouterState } from '@tanstack/react-router';
import { Activity, Container, GitPullRequestArrow, Home, KeyRound, LockKeyhole, RefreshCw, Settings, Workflow } from 'lucide-react';
import { useQueryClient } from '@tanstack/react-query';

import { useTranslation } from 'react-i18next';

import { useApiToken, useOverview } from '@/api/hooks';
import { StatusDot } from '@/components/states';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';

const NAV_ITEMS = [
  { to: '/', label: 'components.nav.dashboard', icon: Home },
  { to: '/projects', label: 'components.nav.projects', icon: GitPullRequestArrow },
  { to: '/runs', label: 'components.nav.runs', icon: Activity },
  { to: '/workspaces', label: 'components.nav.workspaces', icon: Workflow },
  { to: '/resources', label: 'components.nav.resources', icon: Container },
  { to: '/settings', label: 'components.nav.settings', icon: Settings },
] as const;

const VISIBLE_NAV_ITEMS = NAV_ITEMS;

const ROUTE_TITLES: { prefix: string; title: string }[] = [
  { prefix: '/pipelines/', title: 'components.nav.pipelinePreview' },
  { prefix: '/runs/', title: 'components.nav.runDetail' },
  { prefix: '/workspaces/', title: 'components.nav.workspaceDetail' },
  { prefix: '/projects', title: 'components.nav.projects' },
  { prefix: '/pipelines', title: 'components.nav.pipelines' },
  { prefix: '/runs', title: 'components.nav.runHistory' },
  { prefix: '/workspaces', title: 'components.nav.workspaces' },
  { prefix: '/resources', title: 'components.nav.localResources' },
  { prefix: '/settings', title: 'components.nav.settings' },
  { prefix: '/', title: 'components.nav.dashboard' },
];

function AppHeader() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const token = useApiToken();
  const overview = useOverview();
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const title = ROUTE_TITLES.find((route) => route.prefix === pathname)?.title ?? 'Pipedeck';
  const refreshing = queryClient.isFetching() > 0;
  const controlState = overview.isError ? 'offline' : overview.data?.api_status === 'ready' ? 'ready' : 'loading';
  const controlLabel = controlState === 'ready' ? t('components.header.controlReady') : controlState === 'offline' ? t('components.header.controlOffline') : t('components.header.controlConnecting');
  return (
    <header className="flex h-13 min-h-13 shrink-0 items-center justify-between gap-4 border-b border-border bg-[#28272e] px-3 md:px-4">
      <div className="flex min-w-0 items-center gap-2 text-xs text-[#bfbfc3]">
        <strong className="font-semibold text-foreground">Pipedeck</strong>
        <span aria-hidden>/</span>
        <span className="truncate">{t(title)}</span>
      </div>
      <div className="flex min-w-0 items-center gap-2.5">
        <span
          className={cn(
            'hidden items-center gap-1.5 rounded-sm border px-2 py-0.5 text-[11px] sm:flex',
            token.configured ? 'border-[#2f5c48] bg-ok-soft text-ok' : 'border-[#5c4a2f] bg-warn-soft text-warn',
          )}
          title={token.disabledReason ?? t('components.header.tokenLoaded')}
        >
          {token.configured ? <KeyRound className="size-3.5" /> : <LockKeyhole className="size-3.5" />}
          <span>{token.loading ? t('components.header.tokenReading') : token.configured ? t('components.header.tokenLocalWrite') : t('components.header.tokenReadOnly')}</span>
        </span>
        <span className="flex items-center gap-2 text-xs text-muted-foreground">
          <StatusDot active={controlState === 'ready'} />
          <span className="hidden sm:inline">{controlLabel}</span>
          {controlState === 'ready' && <code className="hidden border-l border-border pl-2 font-mono text-[11px] text-[#bfbfc3] md:inline">127.0.0.1:7421</code>}
        </span>
        <button
          type="button"
          aria-label={t('components.header.refreshAll')}
          title={t('components.header.refreshAll')}
          onClick={() => void queryClient.invalidateQueries()}
          className="grid size-8 shrink-0 place-items-center rounded-sm border border-border bg-surface-2 text-muted-foreground hover:text-foreground disabled:opacity-60"
        >
          <RefreshCw className={cn('size-4', refreshing && 'is-spinning')} />
        </button>
      </div>
    </header>
  );
}

function NavLink({ to, label, Icon }: { to: string; label: string; Icon: typeof Home }) {
  const { t } = useTranslation();
  const matchRoute = useMatchRoute();
  const active = Boolean(matchRoute({ to, fuzzy: to !== '/' }));
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Link
          to={to}
          aria-label={t(label)}
          aria-current={active ? 'page' : undefined}
          className={cn(
            'relative grid h-11 w-11 place-items-center rounded-sm text-[#bfbfc3] hover:bg-[#38363f] hover:text-foreground md:h-11 md:w-11',
            active && 'bg-[#4b4956] text-white',
          )}
        >
          <Icon className="size-[19px]" />
          {active && <span aria-hidden className="absolute top-2.5 left-0 hidden h-6 w-0.75 bg-primary md:block" />}
          <span className="hidden text-[10px] md:hidden">{t(label)}</span>
        </Link>
      </TooltipTrigger>
      <TooltipContent side="right">{t(label)}</TooltipContent>
    </Tooltip>
  );
}

/** 应用外壳:桌面左侧主导航,窄屏(≥390)收起为底部导航 */
export function AppShell({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  return (
    <TooltipProvider>
      <div className="flex h-dvh flex-col md:grid md:grid-cols-[60px_minmax(0,1fr)]">
        {/* 桌面左侧导航 */}
        <aside className="hidden flex-col items-center border-r border-[#3f3c46] bg-rail md:flex">
          <div className="grid h-13 w-full place-items-center border-b border-[#3f3c46]">
            <span aria-label="Pipedeck" className="grid size-8 place-items-center rounded-sm bg-primary font-mono text-sm font-bold text-primary-foreground">
              P
            </span>
          </div>
          <nav aria-label={t('components.nav.mainNavigation')} className="grid w-full gap-1 p-2">
            {VISIBLE_NAV_ITEMS.map((item) => (
              <NavLink key={item.to} to={item.to} label={item.label} Icon={item.icon} />
            ))}
          </nav>
          <div className="mt-auto grid h-13 w-full place-items-center border-t border-[#3f3c46] text-[#bfbfc3]">
            <LockKeyhole className="size-4" aria-label={t('components.nav.localOnly')} />
          </div>
        </aside>

        <div className="flex min-w-0 min-h-0 flex-col">
          <AppHeader />
          <main className="min-h-0 flex-1 overflow-hidden pb-14 md:pb-0">{children}</main>
          {/* 窄屏底部导航 */}
          <nav
            aria-label={t('components.nav.mainNavigation')}
            className="fixed inset-x-0 bottom-0 z-50 flex h-14 items-stretch justify-around border-t border-[#3f3c46] bg-rail md:hidden"
          >
            {VISIBLE_NAV_ITEMS.map((item) => (
              <BottomNavLink key={item.to} to={item.to} label={item.label} Icon={item.icon} />
            ))}
          </nav>
        </div>
      </div>
    </TooltipProvider>
  );
}

function BottomNavLink({ to, label, Icon }: { to: string; label: string; Icon: typeof Home }) {
  const { t } = useTranslation();
  const matchRoute = useMatchRoute();
  const active = Boolean(matchRoute({ to, fuzzy: to !== '/' }));
  return (
    <Link
      to={to}
      aria-label={t(label)}
      aria-current={active ? 'page' : undefined}
      className={cn('flex min-w-0 flex-1 flex-col items-center justify-center gap-0.5 text-[10px]', active ? 'text-primary' : 'text-[#bfbfc3]')}
    >
      <Icon className="size-[18px]" />
      <span className="leading-none">{t(label)}</span>
    </Link>
  );
}
