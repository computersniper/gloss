const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright-core');
const base = process.argv[2] || 'http://127.0.0.1:8813';
(async () => {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'glossx-open-folder-ui-'));
  const sameFolder = value => {
    const a = fs.statSync(value), b = fs.statSync(folder);
    return a.dev === b.dev && a.ino === b.ino;
  };
  fs.writeFileSync(path.join(folder, 'existing-notes.txt'), 'Research already in this folder.');
  const browser = await chromium.launch({ executablePath: process.env.BROWSER_PATH || undefined });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
    await context.addInitScript(() => {
      localStorage.setItem('gloss.mode', 'glossx'); localStorage.setItem('gloss.uiLang', 'zh');
      localStorage.setItem('gloss.layout', JSON.stringify({ aiOpen: false, sideOpen: false }));
    });
    const page = await context.newPage(); const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('dialog', dialog => dialog.accept(folder));
    await page.goto(base);
    await page.getByRole('button', { name: /打开工作区/ }).click();
    await page.locator('.wb-side-host').getByText('existing-notes.txt', { exact: true }).waitFor();
    const projects = await (await context.request.get(base + '/api/agent/projects')).json();
    const project = projects.find(p => sameFolder(p.workspace.path));
    assert.ok(project);
    assert.ok(!fs.existsSync(path.join(folder, '.git')), 'opening and status reads do not initialize Git');
    await page.locator('.wb-side-host').getByText('existing-notes.txt', { exact: true }).click();
    await page.locator('textarea').filter({ visible: true }).first().waitFor();
    assert.ok(await page.locator('textarea').evaluateAll(nodes => nodes.some(n => n.value === 'Research already in this folder.')));
    await page.locator('.wb-title-search').click();
    await page.locator('.wb-pal-input').fill('打开文件夹');
    await page.locator('.wb-pal-item').filter({ hasText: '打开文件夹' }).click();
    await page.waitForTimeout(500);
    const again = await (await context.request.get(base + '/api/agent/projects')).json();
    assert.equal(again.filter(p => sameFolder(p.workspace.path)).length, 1);
    assert.equal(fs.readFileSync(path.join(folder, 'existing-notes.txt'), 'utf8'), 'Research already in this folder.');
    assert.deepEqual(errors, []);
    console.log('PASS: open folder from welcome/palette, real project binding, existing file tree/editor, reuse project, preserve files and Git');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
