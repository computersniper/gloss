// Disposable backend only: simulate scanned pairing and a proxy blocking SSE.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright-core');
const base = process.argv[2] || 'http://127.0.0.1:8812';
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.BROWSER_PATH || undefined });
  try {
    const admin = await browser.newContext();
    const token = (await (await admin.request.get(base + '/api/remote')).json()).token;
    const stamp = Date.now();
    const threads = [];
    for (let i = 0; i < 2; i++) {
      const thread = await (await admin.request.post(base + '/api/sessions', { data: { title: `Mobile history ${stamp}-${i}` } })).json();
      const text = `Mobile persisted message ${stamp}-${i}`;
      assert.equal((await admin.request.post(base + `/api/sessions/${thread.id}/messages`, { data: { text } })).status(), 200);
      for (let n = 0; n < 100; n++) {
        if ((await (await admin.request.get(base + `/api/sessions/${thread.id}`)).json()).status === 'idle') break;
        await new Promise(r => setTimeout(r, 100));
      }
      threads.push({ ...thread, text });
    }
    for (const blocked of [true, false]) for (const size of [{ width: 390, height: 844 }, { width: 820, height: 1180 }]) {
      const context = await browser.newContext({ viewport: size, isMobile: true, hasTouch: true, extraHTTPHeaders: { 'x-forwarded-for': '198.51.100.20' } });
      await context.addInitScript(() => {
        localStorage.setItem('gloss.mode', 'glossx'); localStorage.setItem('gloss.uiLang', 'zh');
        localStorage.setItem('gloss.layout', JSON.stringify({ aiOpen: true, aiMode: 'threads', sideOpen: false }));
      });
      if (blocked) await context.route('**/api/sessions/*/events*', route => route.abort());
      await context.route('**/api/settings/quota*', route => route.fulfill({ json: { quotas: [{ provider: 'local_codex', available: true, windows: [{ id: 'five_hour', label: '5h', label_zh: '5小时', used_percent: 23, resets_at: null, window_minutes: 300 }], source: 'fixture' }] } }));
      const page = await context.newPage(); const errors = [];
      page.on('pageerror', e => errors.push(e.message));
      await page.goto(base + '/?token=' + encodeURIComponent(token));
      assert.ok((await context.cookies()).some(c => c.name === 'gloss_token' && c.httpOnly));
      await page.waitForTimeout(500);
      if (size.width <= 720 && !await page.locator('.wb-ai-host').isVisible()) await page.locator('.wb-tabbar').getByRole('button', { name: '智能体', exact: true }).click();
      else await page.getByRole('button', { name: /收件箱/ }).first().click();
      for (const thread of threads) {
        await page.getByRole('tab', { name: '对话列表', exact: true }).click();
        await page.locator('.gx-th-row').filter({ hasText: thread.title }).locator('.gx-th-main').click();
        await page.locator('.gx-chat-scroll').getByText(thread.text, { exact: true }).waitFor({ timeout: 10000 });
        await page.locator('.gx-chat-scroll').getByText('Page reviewed.', { exact: false }).first().waitFor();
      }
      if (blocked && size.width <= 720) {
        const followup = `Mobile polling update ${stamp}`;
        await admin.request.post(base + `/api/sessions/${threads[1].id}/messages`, { data: { text: followup } });
        await page.locator('.gx-chat-scroll').getByText(followup, { exact: true }).waitFor({ timeout: 35000 });
      }
      const quota = page.locator('.wb-titlebar .gx-quota-chip');
      await quota.getByText('5h 23%', { exact: false }).waitFor();
      await quota.click(); await page.getByText('账号额度', { exact: true }).waitFor();
      assert.deepEqual(errors, []);
      await context.close();
    }
    console.log('PASS: paired phone/iPad, history and session switching with normal/blocked SSE, polling new messages, compact quota popup');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
