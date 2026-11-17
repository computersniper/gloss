const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright-core');
const base = process.argv[2] || 'http://127.0.0.1:8807';
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.BROWSER_PATH || undefined });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
    await context.addInitScript(() => {
      localStorage.setItem('gloss.mode', 'glossx'); localStorage.setItem('gloss.uiLang', 'zh');
      localStorage.setItem('gloss.layout', JSON.stringify({ sideOpen: true, activity: 'settings', sideWidth: 500, aiOpen: false, bottomOpen: false }));
    });
    const page = await context.newPage(); const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(base);
    const select = page.getByRole('combobox', { name: 'Codex installation', exact: true });
    await select.waitFor();
    await page.waitForFunction(() => document.querySelector('[aria-label="Codex installation"]')?.options.length > 1);
    const response = await context.request.get(base + '/api/settings/local-cli');
    assert.ok(response.ok()); const detected = await response.json();
    assert.equal(detected.claude_executed, false);
    const items = detected.tools.codex;
    assert.ok(items.length);
    assert.ok((await select.textContent()).includes(items[0].version || items[0].source));
    const selected = items[items.length-1].path;
    await select.selectOption(selected);
    assert.equal(await page.getByRole('textbox', { name: 'Codex CLI path', exact: true }).inputValue(), selected);
    await page.getByRole('button', { name: 'Save settings', exact: true }).click();
    await page.getByRole('button', { name: 'Save settings', exact: true }).waitFor();
    const saved = await (await context.request.get(base + '/api/settings')).json();
    assert.equal(saved.config.providers.local_codex.cli_path, selected);
    await select.selectOption('');
    assert.equal(await page.getByRole('textbox', { name: 'Codex CLI path', exact: true }).inputValue(), '');
    await page.getByRole('button', { name: 'Save settings', exact: true }).click();
    await page.getByRole('button', { name: 'Save settings', exact: true }).waitFor();
    const automatic = await (await context.request.get(base + '/api/settings')).json();
    assert.equal(automatic.config.providers.local_codex.cli_path, '');
    assert.deepEqual(errors, []);
    console.log('PASS: detection API, version/path display, manual selection save/readback, return to automatic; Claude not executed');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
