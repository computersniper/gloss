const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright-core');
const base = process.argv[2] || 'http://127.0.0.1:8813';
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.BROWSER_PATH || undefined });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
    await context.addInitScript(() => {
      localStorage.setItem('gloss.mode', 'glossx'); localStorage.setItem('gloss.uiLang', 'zh');
      localStorage.setItem('gloss.layout', JSON.stringify({ aiOpen: true, aiMode: 'threads', sideOpen: false }));
    });
    const threads = [];
    for (let i = 0; i < 3; i++) threads.push(await (await context.request.post(base + '/api/sessions', { data: { title: `Navigation ${Date.now()}-${i}` } })).json());
    const page = await context.newPage(); const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(base);
    await page.getByRole('tab', { name: '对话列表', exact: true }).waitFor();
    const visit = async (thread) => {
      await page.getByRole('tab', { name: '对话列表', exact: true }).click();
      await page.locator('.gx-th-row').filter({ hasText: thread.title }).locator('.gx-th-main').click();
      await page.locator('.gx-chat').getByText(thread.title, { exact: true }).waitFor();
    };
    await visit(threads[0]); await visit(threads[1]);
    const back = page.getByRole('button', { name: '返回', exact: true });
    const forward = page.getByRole('button', { name: '前进', exact: true });
    await back.click(); await page.locator('.gx-chat').getByText(threads[0].title, { exact: true }).waitFor();
    await forward.click(); await page.locator('.gx-chat').getByText(threads[1].title, { exact: true }).waitFor();
    await back.click(); await visit(threads[2]); assert.ok(await forward.isDisabled(), 'new visit discards forward branch');
    await page.getByRole('button', { name: 'GlossX · 旁注 — 欢迎页', exact: true }).click();
    await back.click(); await page.locator('.gx-chat').getByText(threads[2].title, { exact: true }).waitFor();
    await forward.click(); await page.locator('.wb-ai-host').waitFor({ state: 'hidden' });
    const box = await page.locator('.wb-title-search').boundingBox();
    assert.ok(Math.abs(box.x + box.width / 2 - 720) < 3, 'search is centered in desktop title bar');
    if (process.argv[3]) await page.screenshot({ path: process.argv[3], clip: { x: 0, y: 0, width: 1440, height: 80 } });
    await page.locator('.wb-title-search').click(); await page.locator('.wb-pal').waitFor();
    await page.keyboard.press('Escape');
    for (const width of [1100, 820, 390, 320]) {
      await page.setViewportSize({ width, height: 844 });
      await page.waitForTimeout(100);
      assert.ok(await page.locator('.wb-title-search').isVisible());
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `no horizontal overflow at ${width}`);
    }
    assert.deepEqual(errors, []);
    console.log('PASS: centered search, search opens palette, back/forward between chats, forward branch reset, responsive widths');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
