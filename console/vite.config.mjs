/**
 * The production build, and only the production build.
 *
 * There is no dev server in this project on purpose: `just test` and the console both
 * serve `dist/`, so the bytes that are tested are the bytes that are shipped. A dev
 * server would be a second, differently-built application that nothing here checks.
 *
 * No React plugin either. Its job is fast refresh in a dev server; the JSX transform
 * itself is esbuild's, and that is all a production build needs.
 */

import { defineConfig } from 'vite';
import tailwindcss from '@tailwindcss/vite';

import { bundleTail } from './tools/plugins.mjs';

export default defineConfig({
  plugins: [tailwindcss(), bundleTail()],
  esbuild: { jsx: 'automatic' },
  build: {
    // One JS file and one CSS file. The service worker precaches an explicit list and
    // the console serves an explicit route set, and both of those are simplest when
    // there is nothing to fetch after the first paint.
    cssCodeSplit: false,
    // Nothing inlined as a `data:` URI: an asset the console can name is an asset a
    // test can grep and the worker can cache.
    assetsInlineLimit: 0,
    // No preload shim. It writes a runtime that resolves module URLs, and there is
    // exactly one module here.
    modulePreload: false,
    sourcemap: false,
  },
});
