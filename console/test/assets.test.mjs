/**
 * The bytes themselves: every one of them local, and the manifest that makes this an
 * app rather than a page.
 *
 * A console that fetches from the internet is one the captain cannot use when the
 * internet is what is broken, and a remote script tag is an injection point into a
 * page that reads their whole queue. So this is checked twice and in two different
 * ways, because the two ways fail differently:
 *
 *   - **statically**, that no served byte names an origin that is not this console,
 *     with one exact allowlist and an argument for every entry in it;
 *   - **at runtime**, that a page put through every screen never reaches for anything
 *     off this device, whatever the bytes happen to say.
 *
 * The runtime half is what turns the allowlist from an assertion into a proof. React
 * carries XML namespace names spelled like URLs and cannot be built without them; the
 * static check would let those through on a promise, and this shows they are never
 * dereferenced.
 */

import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';

import { DIST, built, go, load, opened, text, until } from './harness.mjs';
import { decision, obligation, project, snapshot, task } from './fixtures.mjs';

/** XML namespace names. Identifiers that are spelled like URLs and compared as
 * strings; nothing dereferences one. React needs them to create SVG and MathML
 * elements. `tools/plugins.mjs` holds the same list, and the build fails on anything
 * that is not on it. */
const NAMESPACES = [
  'http://www.w3.org/1998/Math/MathML',
  'http://www.w3.org/1999/xhtml',
  'http://www.w3.org/1999/xlink',
  'http://www.w3.org/2000/svg',
  'http://www.w3.org/XML/1998/namespace',
];

function served() {
  const found = [];
  const walk = (at, prefix) => {
    for (const entry of readdirSync(at, { withFileTypes: true })) {
      if (entry.isDirectory()) walk(join(at, entry.name), `${prefix}${entry.name}/`);
      else found.push([`${prefix}${entry.name}`, join(at, entry.name)]);
    }
  };
  walk(DIST, '');
  return found;
}

test('no served byte names an origin that is not this console', async (t) => {
  const offenders = [];
  for (const [name, path] of served()) {
    if (!/\.(js|css|html|webmanifest|json|svg)$/.test(name)) continue;
    const source = readFileSync(path, 'utf8');
    for (const match of source.matchAll(/https?:\/\/[^\s"'`)\\<>]*/g)) {
      if (!NAMESPACES.includes(match[0])) offenders.push(`${name}: ${match[0]}`);
    }
    for (const match of source.matchAll(/["'`]\/\/[A-Za-z0-9]/g)) {
      offenders.push(`${name}: protocol-relative ${match[0]}`);
    }
  }
  assert.deepEqual(offenders, []);
});

test('the shipped page loads nothing but its own two assets', async (t) => {
  const { html } = built();
  const references = [...html.matchAll(/(?:src|href)="([^"]+)"/g)].map((m) => m[1]);
  assert.ok(references.length >= 4, 'the shell references nothing at all');
  for (const reference of references) {
    assert.match(reference, /^\/(assets|icons)\/|^\/manifest\.webmanifest$/,
                 `the shell references ${reference}`);
  }
});

test('the production page initiates no request off this device', async (t) => {
  const reached = [];
  const page = load({
    snapshot: snapshot({
      tasks: [task('a-task', { status: 'blocked', reason: 'ask the captain' })],
      projects: [project('siana')],
      obligations: [obligation('owe-one')],
      decisions: [decision('one-decision')],
    }),
    prepare(window) {
      // Every way a page can reach the network, watched. A namespace literal that was
      // in fact a fetch would land in one of these.
      window.XMLHttpRequest = function XMLHttpRequest() {
        return { open: (method, url) => reached.push(`xhr ${url}`),
                 send: () => {}, setRequestHeader: () => {} };
      };
      window.WebSocket = function WebSocket(url) {
        reached.push(`ws ${url}`);
      };
      window.Image = function Image() {
        return { set src(url) { reached.push(`img ${url}`); } };
      };
      window.navigator.sendBeacon = (url) => {
        reached.push(`beacon ${url}`);
        return true;
      };
      window.importScripts = (url) => reached.push(`worker ${url}`);
    },
  });
  t.after(() => page.close());
  await until(page, (p) => /a-task/.test(text(p)), 'the page never rendered');
  page.streams[0].connect();
  for (const hash of ['#/', '#/projects', '#/project/siana', '#/task/a-task',
                      '#/obligations', '#/decisions', '#/decision/one-decision']) {
    await go(page, hash);
  }
  await page.tick(120_000);
  assert.deepEqual(reached, []);
  for (const request of page.requests) {
    assert.match(request.url, /^\/api\//, `the page fetched ${request.url}`);
  }
  for (const stream of page.streams) {
    assert.match(stream.url, /^\/api\//, `the page streamed from ${stream.url}`);
  }
  // Nothing was added to the document that could fetch on its own either.
  assert.equal(page.document.querySelectorAll(
    'img, iframe, object, embed, video, audio, source, track').length, 0);
  // The shell's own head, unchanged: the stylesheet, the manifest and the two icon
  // links the build put there, and the one script it emitted. Nothing was added.
  for (const node of page.document.querySelectorAll('link')) {
    assert.match(node.getAttribute('href'),
                 /^\/(assets|icons)\/|^\/manifest\.webmanifest$/);
  }
  assert.equal(page.document.querySelectorAll('script').length, 1);
});

test('every class the application renders is in the stylesheet it ships',
  async (t) => {
    // Tailwind emits a rule only for a class it found in the source, so a class name
    // with a typo in it is not a broken rule: it is no rule at all, and the element
    // renders unstyled on a page nobody looked at. There is no browser in this suite
    // to see that with, so it is checked instead.
    const { css } = built();
    const page = await opened(t, {
      snapshot: snapshot({
        tasks: [task('a-task', { status: 'blocked', reason: 'ask the captain' }),
                task('b-task', { status: 'doing', owner: 'claude@w3S:p2' }),
                task('c-task')],
        projects: [project('siana')],
        obligations: [obligation('owe-one', { kind: 'decision' })],
        decisions: [decision('one-decision')],
      }),
    });
    const seen = new Set();
    for (const hash of ['#/', '#/projects', '#/project/siana', '#/task/a-task',
                        '#/obligations', '#/decisions', '#/decision/one-decision']) {
      await go(page, hash);
      for (const node of page.document.body.querySelectorAll('[class]')) {
        for (const token of node.getAttribute('class').split(/\s+/)) {
          if (token) seen.add(token);
        }
      }
    }
    assert.ok(seen.size > 40, `only ${seen.size} classes were rendered at all`);
    const missing = [...seen].filter((token) => {
      const selector = `.${token.replace(/[^A-Za-z0-9_-]/g, (c) => `\\${c}`)}`;
      return !css.includes(selector);
    });
    assert.deepEqual(missing, [], 'these classes style nothing');
  });

test('the manifest is one an app can be installed from', async (t) => {
  const manifest = JSON.parse(built().manifest);
  assert.equal(manifest.start_url, '/');
  assert.equal(manifest.scope, '/');
  assert.equal(manifest.display, 'standalone');
  assert.ok(manifest.name && manifest.short_name);
  assert.ok(manifest.background_color && manifest.theme_color);
  const sizes = manifest.icons.map((icon) => icon.sizes);
  assert.ok(sizes.includes('192x192'), 'no 192px icon, so this will not install');
  assert.ok(sizes.includes('512x512'), 'no 512px icon');
  assert.ok(manifest.icons.some((icon) => icon.purpose === 'maskable'),
            'no maskable icon, so a launcher crops the corners off this one');
  const names = served().map(([name]) => name);
  for (const icon of manifest.icons) {
    assert.match(icon.src, /^\/icons\//);
    assert.ok(names.includes(icon.src.slice(1)),
              `the manifest names ${icon.src}, which was not built`);
    assert.equal(icon.type, 'image/png');
  }
});

test('every icon the manifest names is a real raster image of the size it claims',
  async (t) => {
    for (const icon of JSON.parse(built().manifest).icons) {
      const bytes = readFileSync(join(DIST, icon.src.slice(1)));
      assert.deepEqual([...bytes.subarray(0, 8)],
                       [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a],
                       `${icon.src} is not a PNG`);
      const width = bytes.readUInt32BE(16);
      const height = bytes.readUInt32BE(20);
      assert.equal(`${width}x${height}`, icon.sizes);
    }
  });

test('the build emits exactly the files the console is told to serve', async (t) => {
  const names = served().map(([name]) => name).sort();
  const shape = names.map((name) => name
    .replace(/^assets\/[^/]+\.js$/, 'assets/*.js')
    .replace(/^assets\/[^/]+\.css$/, 'assets/*.css')).sort();
  assert.deepEqual(shape, [
    'assets/*.js',
    'assets/*.css',
    'icons/icon-180.png',
    'icons/icon-192.png',
    'icons/icon-512.png',
    'icons/icon-maskable-512.png',
    'index.html',
    'manifest.webmanifest',
    'sw.js',
  ].sort());
});

test('the worker precaches the assets that were actually built', async (t) => {
  const { sw, jsPath, cssPath } = built();
  const shell = JSON.parse(sw.match(/const SHELL = (\[[^\]]*\]);/)[1]);
  assert.ok(shell.includes(jsPath), 'the worker caches a script that is not this one');
  assert.ok(shell.includes(cssPath));
  assert.match(sw.match(/const CACHE = "([^"]*)";/)[1], /^siana-console-[0-9a-f]{12}$/);
});
