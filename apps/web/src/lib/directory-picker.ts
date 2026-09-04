import { open } from '@tauri-apps/plugin-dialog';

import i18n from '@/i18n';

export const BROWSER_DIRECTORY_PICKER_REASON = (): string => i18n.t('lib.directoryPicker.browserReason');

type TauriWindow = Window & { __TAURI_INTERNALS__?: unknown };
type DirectoryOpen = (options: {
  directory: true;
  multiple: false;
  title: string;
  defaultPath?: string;
}) => Promise<string | string[] | null>;

export function hasNativeDirectoryPicker(candidate: unknown = typeof window === 'undefined' ? undefined : window) {
  return typeof candidate === 'object'
    && candidate !== null
    && Boolean((candidate as TauriWindow).__TAURI_INTERNALS__);
}

export async function pickNativeDirectory(
  { title, currentPath }: { title: string; currentPath?: string },
  dependencies: { runtime?: unknown; openDirectory?: DirectoryOpen } = {},
): Promise<string | null> {
  const runtime = dependencies.runtime ?? (typeof window === 'undefined' ? undefined : window);
  if (!hasNativeDirectoryPicker(runtime)) throw new Error(BROWSER_DIRECTORY_PICKER_REASON());

  const defaultPath = currentPath?.trim();
  const selected = await (dependencies.openDirectory ?? open)({
    directory: true,
    multiple: false,
    title,
    ...(defaultPath ? { defaultPath } : {}),
  });
  return typeof selected === 'string' ? selected : null;
}
