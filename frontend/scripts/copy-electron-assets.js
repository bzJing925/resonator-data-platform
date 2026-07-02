import fs from 'node:fs';
import path from 'node:path';

const srcDir = path.resolve('electron');
const outDir = path.resolve('electron-dist/electron');

for (const file of ['splash.html']) {
  const src = path.join(srcDir, file);
  const dst = path.join(outDir, file);
  if (fs.existsSync(src)) {
    fs.cpSync(src, dst, { force: true });
    console.log(`copied ${src} -> ${dst}`);
  }
}
