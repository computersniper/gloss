const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright-core');
const base = process.argv[2] || 'http://127.0.0.1:8807';
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.BROWSER_PATH || undefined });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
    await context.addInitScript(() => {
      localStorage.setItem('gloss.mode', 'glossx'); localStorage.setItem('gloss.uiLang', 'zh');
      localStorage.setItem('gloss.layout', JSON.stringify({ sideOpen: true, activity: 'projects', sideWidth: 420, aiOpen: true, aiMode: 'threads', bottomOpen: false }));
    });
    const request = context.request;
    const stamp = Date.now();
    const source = await (await request.post(base + '/api/agent/projects', { data: { name: `Move UI source ${stamp}` } })).json();
    const target = await (await request.post(base + '/api/agent/projects', { data: { name: `Move UI target ${stamp}` } })).json();
    const thread = await (await request.post(base + '/api/sessions', { data: { project_id: source.id, title: `Move UI thread ${stamp}` } })).json();
    const page = await context.newPage(); const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(base);
    await page.locator('.gx-proj-row').filter({ hasText: source.name }).click();
    const handle = page.getByRole('separator', { name: '调整项目列表与工作区设置高度' });
    const before = await page.locator('.gx-proj-list').boundingBox();
    const box = await handle.boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down(); await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2 + 90, { steps: 8 }); await page.mouse.up();
    const after = await page.locator('.gx-proj-list').boundingBox();
    assert.ok(after.height > before.height + 60, 'drag expands the upper project list');
    const saved = await page.evaluate(() => localStorage.getItem('glossx.projects.split'));
    await page.reload(); await handle.waitFor();
    assert.equal(await page.evaluate(() => localStorage.getItem('glossx.projects.split')), saved);
    await handle.focus(); await page.keyboard.press('ArrowUp');
    assert.ok(Number(await handle.getAttribute('aria-valuenow')) < Number(saved) * 100);
    const row = page.locator('.gx-th-row').filter({ hasText: thread.title });
    await row.getByTitle('更多', { exact: true }).click();
    await page.getByRole('menuitem', { name: '移动到项目…', exact: true }).click();
    await row.getByRole('combobox', { name: '目标项目', exact: true }).selectOption(target.id);
    await row.getByRole('button', { name: '移动', exact: true }).click();
    await page.locator('.gx-th-group').filter({ has: page.locator('.gx-th-group-name', { hasText: target.name }) }).getByText(thread.title, { exact: true }).waitFor();
    const moved = await (await request.get(base + `/api/sessions/${thread.id}`)).json();
    assert.equal(moved.project_id, target.id);
    assert.equal(moved.workspace.path, target.workspace.path);
    assert.deepEqual(errors, []);
    console.log('PASS: draggable split, saved ratio after reload, keyboard resize, move thread through UI and verify destination workspace');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
