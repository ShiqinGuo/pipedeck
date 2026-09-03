import { CheckCircle2, FolderOpen, TerminalSquare, XCircle } from 'lucide-react';

import { useCatalog, useSession } from '@/api/hooks';
import { CliCommand } from '@/components/cli-command';
import { PageBody, PageHeader, PageScroll } from '@/components/page';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { BusyLabel, ErrorState } from '@/components/states';
import { cli } from '@/lib/cli';

/** CLI 等价命令速查:桌面客户端安装后 CLI 即可用(安装器写 PATH / 应用内重装) */
function CliCard() {
  const commands = [
    { command: cli.serve(), hint: '启动本地控制服务(只绑定 loopback)' },
    { command: cli.status(), hint: '总览:doctor、仓库与中间件摘要' },
    { command: cli.reposList(), hint: '仓库列表' },
    { command: 'pipedeck run <repo> --wait', hint: 'headless 运行仓库管道并等待结束' },
    { command: cli.doctor(), hint: '环境体检:Docker/Git/磁盘' },
  ];
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <TerminalSquare className="size-4 text-primary" />
          CLI
        </CardTitle>
        <CardDescription>GUI 是 CLI 的壳:以下命令与界面操作走同一控制 API。若命令不可用,请重新运行安装器把 CLI 写入 PATH。</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-2">
        {commands.map((entry) => (
          <div key={entry.command} className="grid gap-1">
            <CliCommand command={entry.command} />
            <span className="pl-1 text-[11px] text-muted-foreground">{entry.hint}</span>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

function SessionCard() {
  const session = useSession();
  return (
    <Card data-testid="session-card">
      <CardHeader>
        <CardTitle>会话</CardTitle>
        <CardDescription>本地控制服务的鉴权与写入状态。</CardDescription>
      </CardHeader>
      <CardContent>
        {session.isLoading ? (
          <BusyLabel>正在读取会话</BusyLabel>
        ) : session.isError ? (
          <ErrorState error={session.error} onRetry={() => void session.refetch()} title="无法读取会话信息" />
        ) : (
          <dl className="grid gap-1.5 text-xs">
            <div className="flex items-center gap-2">
              <dt className="text-muted-foreground">写入状态</dt>
              <dd>
                {session.data?.write_enabled ? <Badge variant="ok">已启用(x-pipedeck-token)</Badge> : <Badge variant="warn">只读</Badge>}
              </dd>
            </div>
            <div className="flex items-center gap-2">
              <dt className="text-muted-foreground">鉴权方式</dt>
              <dd className="font-mono text-[11px]">{session.data?.authentication}</dd>
            </div>
          </dl>
        )}
      </CardContent>
    </Card>
  );
}

function ScanRootsCard() {
  const catalog = useCatalog();
  return (
    <Card data-testid="scan-roots-card">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <FolderOpen className="size-4 text-primary" />
          扫描根
        </CardTitle>
        <CardDescription>仓库页的项目目录发现范围;配置由本地控制服务拥有。</CardDescription>
      </CardHeader>
      <CardContent>
        {catalog.isLoading ? (
          <BusyLabel>正在读取扫描根</BusyLabel>
        ) : catalog.isError ? (
          <ErrorState error={catalog.error} onRetry={() => void catalog.refetch()} title="无法读取扫描根" />
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
                全部扫描根读取正常
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
  return (
    <PageScroll>
      <PageHeader eyebrow="SETTINGS" title="设置" description="会话、CLI 状态与扫描根说明。" />
      <PageBody>
        <div className="grid gap-4 lg:grid-cols-2">
          <SessionCard />
          <ScanRootsCard />
        </div>
        <CliCard />
      </PageBody>
    </PageScroll>
  );
}
