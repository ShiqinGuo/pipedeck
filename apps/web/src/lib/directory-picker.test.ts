import { describe, expect, it } from 'vitest';

import { BROWSER_DIRECTORY_PICKER_REASON, hasNativeDirectoryPicker, pickNativeDirectory } from './directory-picker';

describe('lib/directory-picker', () => {
  it('浏览器开发模式抛出 recovery 提示', async () => {
    await expect(pickNativeDirectory({ title: '导入仓库' })).rejects.toThrow(BROWSER_DIRECTORY_PICKER_REASON());
    expect(hasNativeDirectoryPicker({})).toBe(false);
  });

  it('原生环境返回选中的目录并保留取消时的当前值', async () => {
    const tauriWindow = { __TAURI_INTERNALS__: {} };
    expect(hasNativeDirectoryPicker(tauriWindow)).toBe(true);
    const opened: { defaultPath?: string }[] = [];
    const openDirectory = (options: { directory: true; multiple: false; title: string; defaultPath?: string }) => {
      opened.push(options);
      return Promise.resolve('D:\\code\\picked-repository');
    };
    await expect(
      pickNativeDirectory({ title: '导入仓库', currentPath: 'D:\\code\\existing' }, { runtime: tauriWindow, openDirectory }),
    ).resolves.toBe('D:\\code\\picked-repository');
    expect(opened[0]?.defaultPath).toBe('D:\\code\\existing');

    const cancelOpen = () => Promise.resolve(null);
    await expect(
      pickNativeDirectory({ title: '导入仓库' }, { runtime: tauriWindow, openDirectory: cancelOpen }),
    ).resolves.toBeNull();
  });
});
