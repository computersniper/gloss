// Run against scripts/dev/ui_browser_server.py with freshly built frontend/dist.
// Reuse an existing Playwright install and local Edge/Chrome; no product dependency.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright-core');
const base = process.argv[2] || 'http://127.0.0.1:8807';
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.BROWSER_PATH || undefined });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
    const ids = [];
    const titles = ['PDF switch regression A', 'PDF switch regression B'].map(title => `${title} ${Date.now()}`);
    for (const title of titles) {
      const pdf = await context.request.get(`${base}/ui-acceptance.pdf?title=${encodeURIComponent(title)}`);
      const uploaded = await context.request.post(base + '/api/papers/upload', { multipart: { file: { name: title + '.pdf', mimeType: 'application/pdf', buffer: await pdf.body() } } });
      assert.ok(uploaded.ok(), await uploaded.text());
      const data = await uploaded.json(); ids.push((data.paper || data).id);
    }
    await context.addInitScript((ids) => {
      localStorage.setItem('gloss.mode', 'glossx');
      localStorage.setItem('gloss.uiLang', 'en');
      localStorage.setItem('gloss.tabs', JSON.stringify({ tabs: ids.map(id => ({ kind: 'paper', id })), active: `paper:${ids[0]}` }));
      localStorage.setItem('gloss.layout', JSON.stringify({ sideOpen: true, activity: 'library', aiOpen: false, bottomOpen: false }));
    }, ids);
    const page = await context.newPage(); const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(base);
    async function rendered(title) {
      await page.waitForFunction(title => {
        const wrap = document.querySelector('.pdf-wrap');
        const canvas = wrap?.querySelector('canvas');
        if (!wrap?.querySelector('.paper-title')?.textContent.includes(title) || !canvas?.width || !canvas.height) return false;
        const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
        let ink = 0;
        for (let i = 0; i < pixels.length; i += 4) {
          if (pixels[i + 3] && pixels[i] < 180 && pixels[i+1] < 180 && pixels[i+2] < 180) ink++;
        }
        return ink > 100 && wrap.querySelector('.textLayer')?.textContent.includes(title);
      }, title, { timeout: 15000 });
    }
    const a = page.getByRole('tab', { name: 'PDF switch regression A' });
    const b = page.getByRole('tab', { name: 'PDF switch regression B' });
    await rendered('PDF switch regression A');
    await b.click(); await rendered('PDF switch regression B');
    // Cached PDF switches reproduce the unchanged boolean readiness state.
    for (let i=0; i<8; i++) {
      await a.click(); await rendered('PDF switch regression A');
      await b.click(); await rendered('PDF switch regression B');
    }
    // Interrupt rendering by switching while repeatedly changing zoom.
    for (let i=0; i<6; i++) {
      await a.click(); await page.locator('.zoom button').last().click();
      await b.click(); await page.locator('.zoom button').first().click();
    }
    await rendered('PDF switch regression B');
    await b.getByRole('button').click(); await rendered('PDF switch regression A');
    await page.locator('.wb-item').filter({ hasText: titles[1] }).click();
    await rendered('PDF switch regression B');
    // A failed request must produce an actionable error and recover on Retry.
    await a.getByRole('button').click();
    const pdfRoute = `**/api/papers/${ids[0]}/pdf`;
    await page.route(pdfRoute, route => route.fulfill({ status: 503, body: 'Temporary PDF failure' }));
    await page.locator('.wb-item').filter({ hasText: titles[0] }).click();
    await page.getByRole('alert').waitFor();
    await page.unroute(pdfRoute);
    await page.getByRole('button', { name: 'Retry', exact: true }).click();
    await rendered('PDF switch regression A');
    assert.deepEqual(errors, []);
    assert.equal(await page.getByRole('alert').count(), 0);
    console.log('PASS: canvas ink + matching text after 16 cached switches, 12 zoom/switch interruptions, close and reopen, failed load and Retry; no page errors');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
