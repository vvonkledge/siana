/**
 * The app icon, drawn and encoded here rather than committed as a binary.
 *
 * An installable app needs raster icons, and there are three ways to get them: commit
 * PNGs nobody can review, add a rasteriser dependency to a process that will one day
 * face the internet, or draw them. This draws them. It is forty lines of pixel
 * arithmetic and a PNG writer, it has no dependencies, it is deterministic, and the
 * one thing it produces is checked by the same "no remote origin" pass as everything
 * else in the bundle.
 *
 * The mark is the queue: three bars, the top one amber because the top of the queue is
 * the thing that needs the captain.
 */

import { deflateSync } from 'node:zlib';

const CRC_TABLE = (() => {
  const table = new Int32Array(256);
  for (let n = 0; n < 256; n += 1) {
    let c = n;
    for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c;
  }
  return table;
})();

function crc32(buffer) {
  let c = 0xffffffff;
  for (const byte of buffer) c = CRC_TABLE[(c ^ byte) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function chunk(type, data) {
  const head = Buffer.alloc(4);
  head.writeUInt32BE(data.length, 0);
  const body = Buffer.concat([Buffer.from(type, 'latin1'), data]);
  const tail = Buffer.alloc(4);
  tail.writeUInt32BE(crc32(body), 0);
  return Buffer.concat([head, body, tail]);
}

/** 8-bit RGBA, one filter byte per scanline and that filter always `none`.
 *
 * No filtering and no compression level chosen: the icons are a few kilobytes either
 * way, and a build that produces the same bytes on every machine is worth more here
 * than the bytes saved. */
export function encodePng(width, height, rgba) {
  const stride = width * 4;
  const raw = Buffer.alloc((stride + 1) * height);
  for (let y = 0; y < height; y += 1) {
    raw[y * (stride + 1)] = 0;
    rgba.copy(raw, y * (stride + 1) + 1, y * stride, (y + 1) * stride);
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8;
  ihdr[9] = 6;
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr),
    chunk('IDAT', deflateSync(raw, { level: 9 })),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

const BACKDROP = [15, 23, 42];   // slate-900
const BAR = [56, 189, 248];      // sky-400
const TOP_BAR = [251, 191, 36];  // amber-400

/** Coverage of one pixel by a rounded rectangle, sampled rather than computed.
 *
 * Four samples a side. Enough that an edge at this size reads as smooth, and cheap
 * enough that the largest icon is drawn in a few milliseconds. */
function coverage(x, y, rect) {
  let hits = 0;
  for (let sy = 0; sy < 4; sy += 1) {
    for (let sx = 0; sx < 4; sx += 1) {
      if (inside(x + (sx + 0.5) / 4, y + (sy + 0.5) / 4, rect)) hits += 1;
    }
  }
  return hits / 16;
}

function inside(px, py, { left, top, right, bottom, radius }) {
  if (px < left || px > right || py < top || py > bottom) return false;
  const cx = Math.min(Math.max(px, left + radius), right - radius);
  const cy = Math.min(Math.max(py, top + radius), bottom - radius);
  const dx = px - cx;
  const dy = py - cy;
  return dx * dx + dy * dy <= radius * radius;
}

function paint(rgba, width, rect, colour) {
  for (let y = Math.max(0, Math.floor(rect.top));
    y < Math.min(width, Math.ceil(rect.bottom)); y += 1) {
    for (let x = Math.max(0, Math.floor(rect.left));
      x < Math.min(width, Math.ceil(rect.right)); x += 1) {
      const alpha = coverage(x, y, rect);
      if (!alpha) continue;
      const at = (y * width + x) * 4;
      for (let c = 0; c < 3; c += 1) {
        rgba[at + c] = Math.round(rgba[at + c] * (1 - alpha) + colour[c] * alpha);
      }
      rgba[at + 3] = Math.round(rgba[at + 3] * (1 - alpha) + 255 * alpha);
    }
  }
}

/** One icon.
 *
 * `maskable` is a different drawing and not a different size: a maskable icon is
 * cropped to a circle by the launcher, so the backdrop fills the square and the mark
 * sits inside the safe zone. */
export function drawIcon(size, { maskable = false } = {}) {
  const rgba = Buffer.alloc(size * size * 4);
  const pad = maskable ? 0 : size * 0.06;
  paint(rgba, size, {
    left: pad, top: pad, right: size - pad, bottom: size - pad,
    radius: maskable ? 0 : size * 0.22,
  }, BACKDROP);

  const safe = maskable ? size * 0.6 : size * 0.56;
  const originX = (size - safe) / 2;
  const height = safe * 0.16;
  const gap = safe * 0.14;
  const top = (size - (height * 3 + gap * 2)) / 2;
  const widths = [1, 0.72, 0.44];
  widths.forEach((factor, row) => {
    paint(rgba, size, {
      left: originX,
      top: top + row * (height + gap),
      right: originX + safe * factor,
      bottom: top + row * (height + gap) + height,
      radius: height / 2,
    }, row === 0 ? TOP_BAR : BAR);
  });
  return encodePng(size, size, rgba);
}
