import { expect, test } from '@playwright/test';

import type { components } from '../src/api/schema';
import { backendProject, catalogFixture, eventsFixture, failedRunFixture, mockFullApi, runningRunFixture, workspaceFixture } from './api-fixtures';

type WorkspaceRecord = components['schemas']['WorkspaceRecord'];

test('saving retains edits made in flight and the next save uses the new revision', async ({ page }) => {
  await mockFullApi(page);
  let saved: WorkspaceRecord = structuredClone(workspaceFixture);
  let releaseFirstSave: () => void = () => {};
  const firstSaveGate = new Promise<void>((resolve) => { releaseFirstSave = resolve; });
  const writes: Array<components['schemas']['WorkspaceUpdateRequest']> = [];
  await page.route(`**/api/v1/workspaces/${workspaceFixture.id}`, async (route) => {
    if (route.request().method() === 'PUT') {
      const payload = route.request().postDataJSON() as components['schemas']['WorkspaceUpdateRequest'];
      writes.push(payload);
      if (writes.length === 1) await firstSaveGate;
      saved = { ...saved, ...payload, revision: saved.revision + 1 };
    }
    await route.fulfill({ json: saved });
  });
  await page.goto(`/workspaces/${workspaceFixture.id}`);
  const name = page.getByLabel('工作区名称');
  const save = page.getByRole('button', { name: /^(保存中 )?保存$/ });
  await name.fill('已提交的名称');
  await save.click();
  await expect.poll(() => writes.length).toBe(1);
  await name.fill('请求在途时继续编辑');
  await expect(save).toBeDisabled();
  releaseFirstSave();
  await expect(save).toBeEnabled();
  await expect(name).toHaveValue('请求在途时继续编辑');
  await expect(page.getByRole('alert')).toHaveCount(0);
  await save.click();
  await expect.poll(() => writes.length).toBe(2);
  expect(writes[1]?.expected_revision).toBe(workspaceFixture.revision + 1);
  expect(writes[1]?.name).toBe('请求在途时继续编辑');
  await expect(save).toBeDisabled();
  await expect(name).toHaveValue('请求在途时继续编辑');
});

test('removing a service also removes its inbound startup dependencies', async ({ page }) => {
  const workspace: WorkspaceRecord = structuredClone(workspaceFixture);
  workspace.services.push({ ...structuredClone(workspace.services[0]!), project_id: 'project-frontend', depends_on: ['project-backend'], environment: [], connection_profiles: [] });
  const api = await mockFullApi(page, {
    workspaces: { workspaces: [workspace] },
    catalog: { ...catalogFixture, projects: [...catalogFixture.projects, { ...backendProject, id: 'project-frontend', name: '测试前端', requirements: [] }] },
  });
  await page.goto(`/workspaces/${workspace.id}`);
  await page.getByRole('button', { name: '移除 supplier-backend-v2' }).click();
  await page.getByRole('button', { name: '保存', exact: true }).click();
  await expect.poll(() => api.writes.some((write) => write.method === 'PUT')).toBe(true);
  const payload = api.writes.find((write) => write.method === 'PUT')?.body as components['schemas']['WorkspaceUpdateRequest'];
  expect(payload.services).toHaveLength(1);
  expect(payload.services[0]?.project_id).toBe('project-frontend');
  expect(payload.services[0]?.depends_on).toEqual([]);
});

test('event polling advances its cursor and drains late final events before stopping', async ({ page }) => {
  await mockFullApi(page);
  let finished = false;
  let tailAvailable = false;
  let releaseActivePoll: () => void = () => {};
  const activePollGate = new Promise<void>((resolve) => { releaseActivePoll = resolve; });
  const cursors: number[] = [];
  const initialEvent = { ...eventsFixture.events[0]!, message: 'initial-event' };
  const finalEvent = { ...initialEvent, sequence: 2, message: 'final-event' };
  await page.route(`**/api/v1/runs/${runningRunFixture.id}`, (route) => route.fulfill({ json: finished
    ? { ...runningRunFixture, status: 'succeeded', finished_at: runningRunFixture.created_at }
    : runningRunFixture }));
  await page.route(`**/api/v1/runs/${runningRunFixture.id}/events?*`, async (route) => {
    const after = Number(new URL(route.request().url()).searchParams.get('after'));
    cursors.push(after);
    if (cursors.length === 3) {
      // This request started before completion and has an empty response already prepared.
      await activePollGate;
      return route.fulfill({ json: { events: [], next_after: after } });
    }
    const events = [initialEvent, ...(tailAvailable ? [finalEvent] : [])].filter((event) => event.sequence > after);
    return route.fulfill({ json: { events, next_after: events.at(-1)?.sequence ?? after } });
  });
  await page.goto(`/runs/${runningRunFixture.id}`);
  await expect(page.getByTestId('run-log')).toContainText('initial-event');
  await expect.poll(() => cursors.length).toBe(3);
  finished = true;
  await expect(page.getByTestId('run-status-succeeded')).toBeVisible();
  tailAvailable = true;
  releaseActivePoll();
  await expect(page.getByTestId('run-log')).toContainText('final-event');
  expect(cursors.filter((after) => after === 0)).toHaveLength(1);
  await expect.poll(() => cursors.at(-1)).toBe(2);
  await expect(page.getByText('initial-event', { exact: true })).toHaveCount(1);
  await expect(page.getByText('final-event', { exact: true })).toHaveCount(1);
  const completedRequestCount = cursors.length;
  await page.waitForTimeout(1_800);
  expect(cursors).toHaveLength(completedRequestCount);
});

test('a late event response from the previous run cannot appear in the current run', async ({ page }) => {
  await mockFullApi(page);
  let releaseOldEvents: () => void = () => {};
  const oldEventGate = new Promise<void>((resolve) => { releaseOldEvents = resolve; });
  let oldRequestStarted = false;
  await page.route(`**/api/v1/runs/${runningRunFixture.id}/events?*`, async (route) => {
    oldRequestStarted = true;
    await oldEventGate;
    await route.fulfill({ json: { events: [{ ...eventsFixture.events[0]!, message: 'late-old-run-event' }], next_after: 1 } });
  });
  await page.goto(`/runs/${runningRunFixture.id}`);
  await expect.poll(() => oldRequestStarted).toBe(true);
  await page.getByRole('link', { name: '返回列表' }).click();
  await page.getByTestId('run-row').filter({ hasText: failedRunFixture.id }).click();
  await expect(page).toHaveURL(`/runs/${failedRunFixture.id}`);
  await expect(page.getByTestId('run-log').first()).toBeVisible();
  releaseOldEvents();
  await page.waitForTimeout(200);
  await expect(page.getByText('late-old-run-event')).toHaveCount(0);
  await expect(page.getByTestId('log-step-group').filter({ hasText: 'quality' })).toContainText('0 errors, 0 warnings');
});

test('retry opens the returned run and resets the previous step filter', async ({ page }) => {
  await mockFullApi(page);
  await page.goto(`/runs/${failedRunFixture.id}`);
  await page.getByRole('button', { name: 'quality', exact: true }).click();
  await page.getByRole('button', { name: '重试', exact: true }).click();
  await expect(page).toHaveURL('/runs/run-retry-001');
  await expect(page.getByTestId('run-status-queued').first()).toBeVisible();
  await expect(page.getByTestId('log-step-group')).toHaveCount(3);
});
