// Run against scripts/dev/ui_browser_server.py, never a user's live backend.
// A forwarded header simulates a non-local mobile request; native deep links
// are simulated, while pairing/auth/cookies/UI use the real backend and Edge.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright-core');
const backend = process.argv[2] || 'http://127.0.0.1:8810';
const root = path.resolve(__dirname, '../www');
const mime = { '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.webmanifest': 'application/manifest+json' };
(async () => {
  const status = await fetch(backend + '/api/remote');
  assert.equal(status.status, 200, 'disposable backend local settings reachable');
  const code = (await status.json()).token;
  const server = http.createServer((req, res) => {
    const pathname = new URL(req.url, 'http://localhost').pathname;
    const file = path.resolve(root, '.' + (pathname === '/' ? '/index.html' : pathname));
    if (!file.startsWith(root + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) { res.writeHead(404); res.end(); return; }
    res.setHeader('Content-Type', mime[path.extname(file)] || 'application/octet-stream');
    res.end(fs.readFileSync(file));
  });
  await new Promise(r => server.listen(0, '127.0.0.1', r));
  const shell = `http://localhost:${server.address().port}/index.html`;
  let browser;
  try {
    browser = await chromium.launch({ executablePath: process.env.BROWSER_PATH || undefined });
    const context = await browser.newContext({ viewport: { width: 820, height: 1180 }, locale: 'zh-CN', extraHTTPHeaders: { 'x-forwarded-for': '198.51.100.20' } });
    assert.equal((await context.request.get(backend + '/api/health')).status(), 401, 'unpaired mobile request rejected');
    await context.addInitScript(() => {
      localStorage.setItem('gloss.mode', 'glossx'); localStorage.setItem('gloss.uiLang', 'zh');
      // Simulate Capacitor's event bridge only; no actual native hardware.
      window.__appListeners = {};
      window.Capacitor = { Plugins: { App: {
        addListener(event, callback) { window.__appListeners[event] = callback; return Promise.resolve({ remove() {} }); },
        async getLaunchUrl() { return undefined; },
      } } };
    });
    const page = await context.newPage(); const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(shell);
    await page.locator('#addr').fill(backend);
    await page.locator('#code').fill(code);
    await page.locator('#name').fill('Disposable computer');
    await page.locator('#connect').click();
    await page.getByRole('button', { name: '切换电脑', exact: true }).waitFor();
    assert.equal(new URL(page.url()).origin, new URL(backend).origin);
    assert.equal(new URL(page.url()).searchParams.has('token'), false, 'token stripped from URL');
    const cookie = (await context.cookies(backend)).find(c => c.name === 'gloss_token');
    assert.ok(cookie?.httpOnly, 'real backend issued HttpOnly pairing cookie');
    assert.equal(await page.evaluate(async () => (await fetch('/api/health')).status), 200, 'same-origin request uses cookie after token removed');
    assert.equal(await page.evaluate(() => localStorage.getItem('glossx.mobileShell')), shell);
    await page.getByRole('button', { name: '切换电脑', exact: true }).click();
    await page.locator('#add').waitFor();
    assert.match(page.url(), /#switch$/);
    await page.locator('#local').click();
    await page.getByText('GlossX · 设备本地').waitFor();
    await page.getByRole('button', { name: '连接电脑', exact: true }).click();
    await page.locator('#add').waitFor();
    // Warm deep link on the local picker opens the paired computer.
    const deep = 'glossx://pair?' + new URLSearchParams({ u: backend, t: code, n: 'Deep link fixture' });
    await page.evaluate(link => window.__appListeners.appUrlOpen({ url: link }), deep);
    await page.getByRole('button', { name: '切换电脑', exact: true }).waitFor();
    // Deep link while the computer UI is open routes through #pair= back to
    // the picker, then the existing shell handler reconnects.
    await page.evaluate(link => window.__appListeners.appUrlOpen({ url: link }), deep);
    await page.waitForTimeout(800);
    await page.getByRole('button', { name: '切换电脑', exact: true }).waitFor();
    assert.equal(new URL(page.url()).origin, new URL(backend).origin);
    await page.getByRole('button', { name: '切换电脑', exact: true }).click();
    await page.locator('#saved').getByText('Deep link fixture', { exact: true }).waitFor();
    assert.deepEqual(errors, []);
    console.log('PASS: real remote auth 401, manual pairing, HttpOnly cookie, shared frontend, local switch; simulated native deep links on picker + computer UI');
  } finally {
    if (browser) await browser.close();
    await new Promise(r => server.close(r));
  }
})().catch(e => { console.error(e); process.exitCode = 1; });
