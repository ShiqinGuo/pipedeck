import { mkdir } from 'node:fs/promises';
import { resolve } from 'node:path';

import { chromium } from '@playwright/test';

const outputDirectory = resolve(process.cwd(), '../../local-state/visuals');
await mkdir(outputDirectory, { recursive: true });

async function openWorkspace(browser, viewport) {
  const page = await browser.newPage({ viewport });
  await page.goto('http://127.0.0.1:5173');
  await page.locator('.project-row').first().waitFor({ state: 'visible', timeout: 20_000 });
  return page;
}

async function selectProject(page, path) {
  await page.getByLabel('搜索项目').fill(path);
  const suffix = path.replaceAll('\\', '/').split('/').slice(-2).join('/');
  await page.locator('.project-row').filter({ hasText: suffix }).click();
  await page.locator('.service-item').waitFor({ state: 'visible' });
}

async function screenshot(page, name) {
  await page.screenshot({
    path: resolve(outputDirectory, name),
    animations: 'disabled',
  });
}

const browser = await chromium.launch({ channel: 'chrome' });
try {
  const desktop = await openWorkspace(browser, { width: 1440, height: 900 });
  await screenshot(desktop, 'workspace-empty-desktop-v3.png');
  await selectProject(desktop, 'D:\\code\\supplier-backend-v2');
  await screenshot(desktop, 'workspace-supplier-desktop-v3.png');
  await desktop.close();

  const blocked = await openWorkspace(browser, { width: 1440, height: 900 });
  await selectProject(blocked, 'D:\\code\\tripguru-local');
  await blocked.getByRole('button', { name: '运行预检' }).click();
  await blocked.getByRole('dialog', { name: '运行计划预览' }).waitFor({ timeout: 20_000 });
  await screenshot(blocked, 'plan-blocked-desktop-v3.png');
  await blocked.close();

  const ready = await openWorkspace(browser, { width: 1440, height: 900 });
  await selectProject(ready, 'D:\\code\\tg\\ota\\ota-booking-frontend');
  await ready.getByRole('button', { name: '运行预检' }).click();
  await ready.getByRole('dialog', { name: '运行计划预览' }).waitFor({ timeout: 20_000 });
  await screenshot(ready, 'plan-ready-desktop-v3.png');
  await ready.close();

  const compact = await openWorkspace(browser, { width: 1024, height: 768 });
  await selectProject(compact, 'D:\\code\\supplier-backend-v2');
  await screenshot(compact, 'workspace-supplier-1024-v3.png');
  await compact.close();

  const mobileEmpty = await openWorkspace(browser, { width: 390, height: 844 });
  await screenshot(mobileEmpty, 'workspace-empty-mobile-v3.png');
  await mobileEmpty.close();

  const mobileConfigured = await openWorkspace(browser, { width: 390, height: 844 });
  await selectProject(mobileConfigured, 'D:\\code\\supplier-backend-v2');
  await screenshot(mobileConfigured, 'workspace-supplier-mobile-v3.png');
  await mobileConfigured.close();
} finally {
  await browser.close();
}

console.log(outputDirectory);
