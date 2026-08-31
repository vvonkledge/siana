/**
 * The last three things a production build does, after the bundler has finished.
 *
 * All three run on the bytes that will actually be served, off disk, rather than on
 * anything the bundler holds in memory. That is deliberate: what a test greps and
 * what a captain's browser fetches is the file in `dist/`, and a check against an
 * intermediate representation would be a check against something nobody ever serves.
 *
 *   1. the icons, drawn (`png.mjs`)
 *   2. the service worker, written against the file names that were actually emitted
 *   3. the remote-origin pass, which is the one that can fail the build
 *
 * The third is the point of this file. Every asset this console serves has to be
 * local: a console that fetches from the internet is one the captain cannot use when
 * the internet is what is broken, and a remote script tag is an injection point into a
 * page that reads the captain's queue. Rather than trusting that nobody adds a font
 * link, the build refuses to emit a byte carrying an origin that is not on the list
 * below.
 */

import { createHash } from 'node:crypto';
import { mkdirSync, readdirSync, readFileSync, statSync, writeFileSync }
  from 'node:fs';
import { dirname, join, relative, resolve, sep } from 'node:path';

import { drawIcon } from './png.mjs';

const ICONS = [
  ['icons/icon-180.png', 180, {}],
  ['icons/icon-192.png', 192, {}],
  ['icons/icon-512.png', 512, {}],
  ['icons/icon-maskable-512.png', 512, { maskable: true }],
];

/** The only absolute origins allowed to survive into a served file.
 *
 * All four are XML namespace names. A namespace name is an identifier that happens to
 * be spelled like a URL: the DOM compares it as a string when it decides which
 * document tree an element belongs in, and nothing ever dereferences one. React needs
 * them to create SVG and MathML elements, so they cannot be removed, and they are on
 * this list because they are provably not fetches rather than because they look
 * harmless.
 *
 * `test/assets.test.mjs` holds the runtime half of that proof: the production page is
 * loaded with every network primitive instrumented, and nothing reaches for any of
 * them.
 */
const NAMESPACES = new Set([
  'http://www.w3.org/1998/Math/MathML',
  'http://www.w3.org/1999/xhtml',
  'http://www.w3.org/1999/xlink',
  'http://www.w3.org/2000/svg',
  'http://www.w3.org/XML/1998/namespace',
]);

/** Origins a dependency writes into its own output, and what they become instead.
 *
 * Neither is fetched by anything: the first is a licence banner in a CSS comment, the
 * second is interpolated into the text of a thrown `Error` so that a developer can
 * look the code up. They are rewritten rather than allowed, because "it is only in a
 * comment" and "it is only in an error message" are exactly the arguments that would
 * later be made for a real one. What is left still says where to look.
 */
const REWRITES = [
  [/\/\*![^*]*\*+(?:[^/*][^*]*\*+)*\//g, ''],
  [/https:\/\/react\.dev\/errors\//g, 'react.dev/errors/'],
];

const TEXT = new Set(['.js', '.css', '.html', '.webmanifest', '.json', '.svg']);

function walk(root, at = root) {
  const found = [];
  for (const entry of readdirSync(at).sort()) {
    const full = join(at, entry);
    if (statSync(full).isDirectory()) found.push(...walk(root, full));
    else found.push(relative(root, full).split(sep).join('/'));
  }
  return found;
}

function write(root, name, body) {
  const file = join(root, name);
  mkdirSync(dirname(file), { recursive: true });
  writeFileSync(file, body);
}

/** Every remote origin a file still names, after the rewrites above. */
function remoteOrigins(text) {
  const found = new Set();
  for (const match of text.matchAll(/https?:\/\/[^\s"'`)\\<>]*/g)) {
    if (!NAMESPACES.has(match[0])) found.add(match[0]);
  }
  // A protocol-relative URL is a remote origin that a scheme-only grep walks past,
  // and it is the form a CDN snippet is most often pasted in.
  for (const match of text.matchAll(/["'`]\/\/[A-Za-z0-9]/g)) found.add(match[0]);
  return [...found];
}

export function bundleTail() {
  let outDir = 'dist';
  let root = '.';
  return {
    name: 'siana:bundle-tail',
    configResolved(config) {
      root = config.root;
      // Resolved rather than joined, so a build asked for an output directory
      // outside the project - which is how the suite builds twice and compares -
      // lands where it was asked to.
      outDir = resolve(config.root, config.build.outDir);
    },
    closeBundle() {
      for (const [name, size, options] of ICONS) {
        write(outDir, name, drawIcon(size, options));
      }

      const files = walk(outDir);
      // The shell is what has to be there with no console to ask: the one application
      // path, the manifest, the icons, and the generated assets. `sw.js` is not in it
      // - the browser owns fetching that, and a worker that cached itself would be
      // one an upgrade could not replace.
      const shell = ['/', '/manifest.webmanifest',
                     ...files.filter((f) => f.startsWith('assets/')
                       || f.startsWith('icons/')).map((f) => `/${f}`)];
      const source = readFileSync(join(root, 'src', 'sw.js'), 'utf8');
      const cache = `siana-console-${createHash('sha256')
        .update(shell.join('\n')).update(source).digest('hex').slice(0, 12)}`;
      write(outDir, 'sw.js', source
        .replace("'__SIANA_SHELL__'", JSON.stringify(shell))
        .replace("'__SIANA_CACHE__'", JSON.stringify(cache)));

      const offenders = [];
      for (const name of walk(outDir)) {
        const dot = name.lastIndexOf('.');
        if (!TEXT.has(name.slice(dot))) continue;
        const file = join(outDir, name);
        let text = readFileSync(file, 'utf8');
        for (const [pattern, replacement] of REWRITES) {
          text = text.replace(pattern, replacement);
        }
        writeFileSync(file, text);
        for (const origin of remoteOrigins(text)) {
          offenders.push(`  ${name}: ${origin}`);
        }
      }
      if (offenders.length) {
        throw new Error(
          'this build names an origin that is not this console:\n'
          + `${offenders.join('\n')}\n`
          + 'every asset the console serves has to be local, so nothing here can\n'
          + 'reach the internet and nothing can be injected through a remote\n'
          + 'script. Vendor it, or add it to NAMESPACES in tools/plugins.mjs with\n'
          + 'the argument for why it is not a fetch.');
      }
    },
  };
}
