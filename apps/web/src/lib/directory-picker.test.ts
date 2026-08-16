import { describe, expect, it, vi } from 'vitest';

import {
  BROWSER_DIRECTORY_PICKER_REASON,
  hasNativeDirectoryPicker,
  pickNativeDirectory,
} from './directory-picker';

const tauriRuntime = { __TAURI_INTERNALS__: {} };

describe('native directory picker', () => {
  it('only reports availability inside a Tauri runtime', () => {
    expect(hasNativeDirectoryPicker({})).toBe(false);
    expect(hasNativeDirectoryPicker(tauriRuntime)).toBe(true);
  });

  it('opens a single-directory dialog with the existing path as its default', async () => {
    const openDirectory = vi.fn().mockResolvedValue('D:\\code\\selected');

    await expect(pickNativeDirectory(
      { title: '选择目录', currentPath: ' D:\\code ' },
      { runtime: tauriRuntime, openDirectory },
    )).resolves.toBe('D:\\code\\selected');
    expect(openDirectory).toHaveBeenCalledWith({
      directory: true,
      multiple: false,
      title: '选择目录',
      defaultPath: 'D:\\code',
    });
  });

  it('returns null when selection is cancelled so the caller can preserve its input', async () => {
    await expect(pickNativeDirectory(
      { title: '选择目录', currentPath: 'D:\\code\\existing' },
      { runtime: tauriRuntime, openDirectory: vi.fn().mockResolvedValue(null) },
    )).resolves.toBeNull();
  });

  it('rejects with an actionable browser-mode reason without invoking the plugin', async () => {
    const openDirectory = vi.fn();

    await expect(pickNativeDirectory(
      { title: '选择目录' },
      { runtime: {}, openDirectory },
    )).rejects.toThrow(BROWSER_DIRECTORY_PICKER_REASON);
    expect(openDirectory).not.toHaveBeenCalled();
  });
});
