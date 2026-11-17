const assert = require('node:assert/strict');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright-core');
const base = process.argv[2] || 'http://127.0.0.1:8798';
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.BROWSER_PATH || undefined });
  try {
    for (const width of [220, 260, 320]) {
      const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
      await context.addInitScript(width => {
        localStorage.setItem('gloss.mode', 'glossx'); localStorage.setItem('gloss.uiLang', 'zh');
        localStorage.setItem('gloss.layout', JSON.stringify({ activity: 'capabilities', sideOpen: true, sideWidth: width, aiOpen: false, bottomOpen: false }));
      }, width);
      const page = await context.newPage(); const errors = []; page.on('pageerror', e => errors.push(e.message));
      await page.goto(base); await page.locator('.extensions-heading-actions').waitFor();
      await page.locator('.wb-section').first().locator('summary').click();
      await page.locator('.extensions-heading').scrollIntoViewIfNeeded();
      const result = await page.evaluate(() => {
        const title = document.querySelector('.extensions-heading > div:first-child strong');
        const range = document.createRange(); range.selectNodeContents(title);
        const pane = document.querySelector('.wb-side-host').getBoundingClientRect();
        return { title: title.textContent, lines: range.getClientRects().length, buttons: [...document.querySelectorAll('.extensions-heading-actions button, .extension-actions button')].map(b => { const r = b.getBoundingClientRect(); return { text: b.textContent, clipped: b.scrollWidth > b.clientWidth, outside: r.left < pane.left || r.right > pane.right, nowrap: getComputedStyle(b).whiteSpace }; }) };
      });
      assert.equal(result.title, '插件市场'); assert.equal(result.lines, 1);
      for (const button of result.buttons) { assert.equal(button.clipped, false, button.text); assert.equal(button.outside, false, button.text); assert.equal(button.nowrap, 'nowrap'); }
      assert.deepEqual(errors, []);
      if (width === 260) await page.screenshot({ path: path.resolve('docs/screenshots/plugins-zh-narrow.png') });
      console.log('PASS Chinese capabilities/plugins sidebar width', width);
      await context.close();
    }
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
