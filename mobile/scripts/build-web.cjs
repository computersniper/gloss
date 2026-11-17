// Bundle the same React entry with relative asset URLs inside the native shell.
// Build directly into the ignored local directory; do not alter frontend/dist.
const { spawnSync } = require('node:child_process');
const { resolve } = require('node:path');
const frontend = resolve(__dirname, '../../frontend');
const result = spawnSync(process.execPath, [
  resolve(frontend, 'node_modules/vite/bin/vite.js'),
  'build', '--base', './', '--outDir', '../mobile/www/local', '--emptyOutDir',
], { cwd: frontend, stdio: 'inherit' });
if (result.error) throw result.error;
process.exit(result.status ?? 1);
