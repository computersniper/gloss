// Re-render every raster copy of the GlossX mark from docs/logo-x.svg:
// the Windows .ico, the PWA icons, and the iOS / Android icons and splash
// screens. Each existing PNG is redrawn in place: same size, background and
// corner shape, with the mark at the size and position the old one had.
//
//   NODE_PATH=<dir with playwright> node scripts/dev/render_brand_assets.cjs [chromium executable]
//
// `oldBounds` describes where the previous mark's strokes sat in its 96-unit
// viewBox; update it together with `newBounds` when the mark changes again.
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..", "..");
const svg = fs.readFileSync(path.join(ROOT, "docs/logo-x.svg"), "utf8");
const oldBounds = { x0: 15.7, y0: 3.8, x1: 92.2, y1: 86.3 };  // smaller X at the lower right
const newBounds = { x0: 15.7, y0: 3.8, x1: 92.2, y1: 86.5 };  // larger X under the lamp and its rays

const walk = (dir) => fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
  const p = path.join(dir, e.name);
  return e.isDirectory() ? walk(p) : [p];
});
const targets = [
  ...["frontend/public/icon-192.png", "frontend/public/icon-512.png", "frontend/public/apple-touch-icon.png",
    "mobile/assets/icon.png", "mobile/assets/splash.png", "mobile/assets/splash-dark.png"],
  ...walk(path.join(ROOT, "mobile/android/app/src/main/res")).filter((p) => /(ic_launcher(_round|_foreground)?|splash)\.png$/.test(p)).map((p) => path.relative(ROOT, p)),
  ...walk(path.join(ROOT, "mobile/ios/App/App/Assets.xcassets")).filter((p) => p.endsWith(".png")).map((p) => path.relative(ROOT, p)),
].filter((p) => fs.existsSync(path.join(ROOT, p)));

async function redraw(page, file) {
  const data = fs.readFileSync(path.join(ROOT, file)).toString("base64");
  const out = await page.evaluate(async ({ data, svg, oldBounds, newBounds }) => {
    const load = (src) => new Promise((ok, err) => { const i = new Image(); i.onload = () => ok(i); i.onerror = err; i.src = src; });
    const old = await load(`data:image/png;base64,${data}`);
    const W = old.naturalWidth, H = old.naturalHeight;
    const c = document.createElement("canvas"); c.width = W; c.height = H;
    const g = c.getContext("2d");
    g.drawImage(old, 0, 0);
    const px = g.getImageData(0, 0, W, H).data;
    const at = (x, y) => { const i = (Math.floor(y) * W + Math.floor(x)) * 4; return [px[i], px[i + 1], px[i + 2], px[i + 3]]; };
    // background: the most common opaque colour along an inner ring
    const votes = new Map();
    for (let k = 0; k < 64; k++) {
      const a = (k / 64) * Math.PI * 2;
      const [r, gg, b, al] = at(W / 2 + Math.cos(a) * W * 0.42, H / 2 + Math.sin(a) * H * 0.42);
      if (al > 250) { const key = `${r},${gg},${b}`; votes.set(key, (votes.get(key) || 0) + 1); }
    }
    const bg = [...votes.entries()].sort((x, y) => y[1] - x[1])[0]?.[0].split(",").map(Number) || [17, 20, 26];
    // the old mark: strongly coloured pixels
    let x0 = W, y0 = H, x1 = -1, y1 = -1;
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
      const i = (y * W + x) * 4;
      if (px[i + 3] < 200) continue;
      if (Math.abs(px[i] - bg[0]) + Math.abs(px[i + 1] - bg[1]) + Math.abs(px[i + 2] - bg[2]) < 140) continue;
      if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y;
    }
    if (x1 < 0) return null;
    const s = (y1 - y0) / (oldBounds.y1 - oldBounds.y0);
    const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
    const ox = cx - s * (newBounds.x0 + newBounds.x1) / 2, oy = cy - s * (newBounds.y0 + newBounds.y1) / 2;
    // keep the old shape (alpha), fill it with the background, draw the new mark
    g.globalCompositeOperation = "source-atop";
    g.fillStyle = `rgb(${bg.join(",")})`; g.fillRect(0, 0, W, H);
    g.globalCompositeOperation = "source-over";
    const size = 96 * s;
    // small renders get thicker strokes so the page and the X survive
    const k = size < 40 ? 1.35 : size < 80 ? 1.15 : 1;
    const mark = svg.replace(/stroke-width="([\d.]+)"/g, (_m, w) => `stroke-width="${(Number(w) * k).toFixed(2)}"`)
      .replace("<svg ", `<svg width="${size}" height="${size}" `);
    const img = await load(`data:image/svg+xml;base64,${btoa(unescape(encodeURIComponent(mark)))}`);
    g.drawImage(img, ox, oy, size, size);
    return c.toDataURL("image/png").split(",")[1];
  }, { data, svg, oldBounds, newBounds });
  if (!out) { console.log("skip (no mark found):", file); return; }
  fs.writeFileSync(path.join(ROOT, file), Buffer.from(out, "base64"));
  console.log("redrew", file);
}

async function ico(page) {
  const sizes = [16, 24, 32, 48, 64, 128, 256];
  const pngs = [];
  for (const s of sizes) {
    const k = s <= 24 ? 1.45 : s <= 48 ? 1.2 : 1;
    const mark = svg.replace(/stroke-width="([\d.]+)"/g, (_m, w) => `stroke-width="${(Number(w) * k).toFixed(2)}"`)
      .replace("<svg ", `<svg width="${s}" height="${s}" `);
    await page.setViewportSize({ width: s, height: s });
    await page.setContent(`<body style="margin:0;background:transparent">${mark}</body>`);
    pngs.push(await page.screenshot({ omitBackground: true }));
  }
  const head = Buffer.alloc(6); head.writeUInt16LE(0, 0); head.writeUInt16LE(1, 2); head.writeUInt16LE(sizes.length, 4);
  let offset = 6 + 16 * sizes.length; const entries = [];
  sizes.forEach((s, i) => {
    const e = Buffer.alloc(16); e.writeUInt8(s === 256 ? 0 : s, 0); e.writeUInt8(s === 256 ? 0 : s, 1);
    e.writeUInt16LE(1, 4); e.writeUInt16LE(32, 6); e.writeUInt32LE(pngs[i].length, 8); e.writeUInt32LE(offset, 12);
    offset += pngs[i].length; entries.push(e);
  });
  fs.writeFileSync(path.join(ROOT, "packaging/gloss.ico"), Buffer.concat([head, ...entries, ...pngs]));
  console.log("wrote packaging/gloss.ico");
}

(async () => {
  const browser = await chromium.launch(process.argv[2] ? { executablePath: process.argv[2] } : {});
  const page = await browser.newPage();
  await page.setContent("<body></body>");
  for (const file of targets) await redraw(page, file);
  await ico(page);
  await browser.close();
})();
