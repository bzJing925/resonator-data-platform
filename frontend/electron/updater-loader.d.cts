import type { AppUpdater } from 'electron-updater';

declare module './updater-loader.cjs' {
  export const autoUpdater: AppUpdater;
}
