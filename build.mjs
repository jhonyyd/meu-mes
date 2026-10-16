import { build } from 'esbuild';
import { mkdir, copyFile } from 'node:fs/promises';
await mkdir('web/dist', { recursive: true });
await build({ entryPoints: ['web/src/app.jsx'], bundle: true, minify: true, outfile: 'web/dist/app.js', jsx: 'automatic', legalComments: 'external' });
await copyFile('web/index.html', 'web/dist/index.html');
await copyFile('web/src/style.css', 'web/dist/style.css');
