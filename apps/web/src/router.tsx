import { createRootRoute, createRoute, createRouter, Outlet } from '@tanstack/react-router';

import { AppShell } from '@/components/app-shell';
import DashboardRoute from '@/routes/dashboard';
import PipelineRoute from '@/routes/pipeline';
import ProjectsRoute from '@/routes/projects';
import RunDetailRoute from '@/routes/run-detail';
import RunsRoute from '@/routes/runs';
import SettingsRoute from '@/routes/settings';
import WorkspaceDetailRoute from '@/routes/workspace-detail';
import WorkspacesRoute from '@/routes/workspaces';
import ResourcesRoute from '@/routes/resources';

const rootRoute = createRootRoute({
  component: () => (
    <AppShell>
      <Outlet />
    </AppShell>
  ),
});

const dashboardRoute = createRoute({ getParentRoute: () => rootRoute, path: '/', component: DashboardRoute });
const projectsRoute = createRoute({ getParentRoute: () => rootRoute, path: '/projects', component: ProjectsRoute });
const pipelineRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/pipelines/$repoId',
  component: PipelineRoute,
});
const runsRoute = createRoute({ getParentRoute: () => rootRoute, path: '/runs', component: RunsRoute });
const runDetailRoute = createRoute({ getParentRoute: () => rootRoute, path: '/runs/$runId', component: RunDetailRoute });
const workspacesRoute = createRoute({ getParentRoute: () => rootRoute, path: '/workspaces', component: WorkspacesRoute });
const workspaceDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/workspaces/$id',
  component: WorkspaceDetailRoute,
});
const resourcesRoute = createRoute({ getParentRoute: () => rootRoute, path: '/resources', component: ResourcesRoute });
const settingsRoute = createRoute({ getParentRoute: () => rootRoute, path: '/settings', component: SettingsRoute });

const routeTree = rootRoute.addChildren([
  dashboardRoute,
  projectsRoute,
  pipelineRoute,
  runsRoute,
  runDetailRoute,
  workspacesRoute,
  workspaceDetailRoute,
  resourcesRoute,
  settingsRoute,
]);

export const router = createRouter({ routeTree });

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router;
  }
}
