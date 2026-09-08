import { expect, test } from '@playwright/test';

interface CatalogProject {
  id: string;
  name: string;
  path: string;
  requirements: string[];
}

interface CatalogResponse {
  projects: CatalogProject[];
}

test('real local control service and supplier catalog are visible from the client', async ({ page, request }, testInfo) => {
  test.skip(testInfo.project.name !== 'desktop', 'The real-machine smoke runs once on desktop.');

  const response = await request.get('http://127.0.0.1:7421/api/v1/catalog/projects');
  expect(response.ok()).toBe(true);
  const catalog = (await response.json()) as CatalogResponse;
  const supplier = catalog.projects.find(
    (project) => project.path.replaceAll('/', '\\').toLowerCase() === 'd:\\code\\supplier-backend-v2',
  );
  expect(supplier, 'The current machine must discover D:\\code\\supplier-backend-v2').toBeTruthy();

  await page.goto('/');
  await expect(page.getByRole('heading', { name: '本地 CI/CD 控制台' })).toBeVisible();
  await expect(page.getByText('本地控制服务', { exact: true })).toBeVisible();
  await expect(page.getByLabel('主导航').getByRole('button')).toHaveCount(5);
});
