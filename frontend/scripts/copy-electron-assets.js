import fs from 'node:fs';
import path from 'node:path';

const srcDir = path.resolve('electron');
const outDir = path.resolve('electron-dist/electron');

for (const file of ['splash.html', 'updater-loader.cjs']) {
  const src = path.join(srcDir, file);
  const dst = path.join(outDir, file);
  if (fs.existsSync(src)) {
    fs.cpSync(src, dst, { force: true });
    console.log(`copied ${src} -> ${dst}`);
  }
}

// preload 必须编译为 CommonJS，但 tsc 输出 .js；在 ESM package 中会被当成 ESM，
// 导致 Electron sandbox preload 报 "Cannot use import statement"。重命名为 .cjs。
const preloadJs = path.join(outDir, 'preload.js');
const preloadCjs = path.join(outDir, 'preload.cjs');
if (fs.existsSync(preloadJs)) {
  fs.renameSync(preloadJs, preloadCjs);
  console.log(`renamed ${preloadJs} -> ${preloadCjs}`);
}
