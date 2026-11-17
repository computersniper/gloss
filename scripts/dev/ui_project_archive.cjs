// Real UI + disposable backend acceptance; never touches the user's library.
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
    const project = await (await request.post(base + '/api/agent/projects', { data: { name: 'Archive UI fixture' } })).json();
    const thread = await (await request.post(base + '/api/sessions', { data: { project_id: project.id, title: 'Archive UI thread' } })).json();
    const page = await context.newPage(); const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(base);
    const row = page.locator('.gx-proj-row').filter({ hasText: 'Archive UI fixture' });
    await row.click();
    const bottom = page.locator('.gx-projects > .wb-project');
    await bottom.getByText('工作区设置', { exact: true }).waitFor();
    assert.ok((await bottom.textContent()).includes('Archive UI fixture'));
    const listBox = await page.locator('.gx-proj-list').boundingBox();
    const detailBox = await bottom.boundingBox();
    assert.ok(detailBox.y >= listBox.y + listBox.height - 1, 'workspace settings below project list');
    await row.getByTitle('项目操作', { exact: true }).click();
    await page.getByRole('menuitem', { name: '归档', exact: true }).click();
    await page.getByRole('button', { name: '归档', exact: true }).click();
    await row.waitFor({ state: 'hidden' });
    assert.ok((await (await request.get(base + '/api/sessions?archived=true')).json()).some(s => s.id === thread.id));
    await page.locator('.gx-project-switch').getByRole('radio', { name: '归档', exact: true }).click();
    const archived = page.locator('.gx-proj-row').filter({ hasText: 'Archive UI fixture' });
    await archived.waitFor();
    await archived.getByTitle('项目操作', { exact: true }).click();
    await page.getByRole('menuitem', { name: '恢复', exact: true }).click();
    await archived.waitFor({ state: 'hidden' });
    assert.ok((await (await request.get(base + '/api/sessions')).json()).some(s => s.id === thread.id));
    // Thread archive is reachable and allows permanent deletion only there.
    await page.getByTitle('归档对话', { exact: true }).click();
    await request.patch(base + `/api/sessions/${thread.id}`, { data: { archived: true } });
    await page.getByTitle('归档对话', { exact: true }).click();
    await page.getByTitle('归档对话', { exact: true }).click();
    const threadRow = page.locator('.gx-th-row').filter({ hasText: 'Archive UI thread' });
    await threadRow.waitFor();
    await threadRow.getByTitle('更多', { exact: true }).click();
    await page.getByRole('menuitem', { name: '永久删除', exact: true }).click();
    await page.getByRole('button', { name: '删除', exact: true }).click();
    await threadRow.waitFor({ state: 'hidden' });
    assert.equal((await request.get(base + `/api/sessions/${thread.id}`)).status(), 404);
    assert.deepEqual(errors, []);
    console.log('PASS: separate workspace settings, project archive/restore with threads, thread archive/delete; no model calls');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
