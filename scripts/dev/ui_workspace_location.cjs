// Real HTTP/UI acceptance for a disposable ui_browser_server.py instance only.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright-core');
const base = process.argv[2] || 'http://127.0.0.1:8798';
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.BROWSER_PATH || undefined });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const api = async route => { const r = await context.request.get(base + route); assert.ok(r.ok(), await r.text()); return r.json(); };
    const settings = await api('/api/harness/settings');
    assert.match(settings.paths.home, /glossx-ui-browser-/); // never point this acceptance at real user data
    const root = path.resolve(settings.paths.data, '..');
    const newBase = path.join(root, 'paper-default-' + Date.now());
    const target = path.join(root, 'relocated-' + Date.now());
    const papers = await api('/api/papers');
    const project = await api(`/api/papers/${papers.papers[0].id}/project`);
    await context.addInitScript(({ projectId }) => {
      localStorage.setItem('gloss.mode', 'glossx'); localStorage.setItem('gloss.uiLang', 'en'); localStorage.setItem('gloss.theme', 'daylight');
      localStorage.setItem('gloss.agentProject', projectId);
      localStorage.setItem('gloss.layout', JSON.stringify({ sideOpen: true, activity: 'settings', aiOpen: false, bottomOpen: false, sideWidth: 340 }));
    }, { projectId: project.id });
    const page = await context.newPage(); const errors = []; page.on('pageerror', e => errors.push(e.message));
    await page.goto(base);
    await page.getByRole('textbox', { name: 'Default folder for new paper workspaces', exact: true }).fill(newBase);
    await Promise.all([page.waitForResponse(r => r.url().endsWith('/api/settings') && r.request().method() === 'POST'), page.getByRole('button', { name: 'Save settings', exact: true }).click()]);
    assert.equal((await api('/api/settings')).config.paper_workspace_root, newBase);
    assert.equal((await api(`/api/agent/projects/${project.id}`)).workspace.path, project.workspace.path);
    const pdf = await context.request.get(base + '/ui-acceptance.pdf?title=New%20default%20workspace%20paper');
    const uploaded = await context.request.post(base + '/api/papers/upload', { multipart: { file: { name: 'new-default.pdf', mimeType: 'application/pdf', buffer: await pdf.body() } } });
    assert.ok(uploaded.ok(), await uploaded.text()); const body = await uploaded.json();
    const paper = body.paper || body;
    const created = await api(`/api/papers/${paper.id}/project`);
    assert.ok(created.workspace.path.startsWith(newBase), 'new paper workspace must use saved default');
    await page.getByRole('button', { name: 'Workspace', exact: true }).first().click();
    await page.locator('.gx-ws-location summary').click();
    const field = page.getByRole('textbox', { name: 'Project workspace folder', exact: true });
    await field.fill(target);
    await page.getByRole('button', { name: 'Change location…', exact: true }).click();
    await page.getByRole('alertdialog', { name: 'Change workspace location?' }).waitFor();
    assert.match(await page.getByRole('alertdialog').innerText(), /original folder stays in place/);
    await page.getByRole('button', { name: 'Cancel', exact: true }).click();
    assert.equal((await api(`/api/agent/projects/${project.id}`)).workspace.path, project.workspace.path);
    await page.getByRole('button', { name: 'Change location…', exact: true }).click();
    const [relocated] = await Promise.all([page.waitForResponse(r => r.url().endsWith(`/api/workspace/${project.id}/location`)), page.getByRole('button', { name: 'Copy and switch', exact: true }).click()]);
    assert.ok(relocated.ok(), await relocated.text());
    await page.waitForFunction(target => document.querySelector('.gx-ws-location input')?.value === target && document.querySelector('.gx-ws-location button')?.disabled, target);
    assert.equal((await api(`/api/agent/projects/${project.id}`)).workspace.path, target);
    const original = await fs.readFile(path.join(project.workspace.path, 'GLOSSX.md'));
    assert.deepEqual(await fs.readFile(path.join(target, 'GLOSSX.md')), original);
    await fs.access(path.join(target, '.git')); await fs.access(path.join(project.workspace.path, '.git'));
    await page.locator('.gx-ws-location summary').waitFor();
    await page.locator('.gx-ws-location').evaluate(details => { details.open = true; });
    await page.waitForTimeout(800);
    await page.screenshot({ path: path.resolve('docs/screenshots/workspace-location-daylight.png') });
    assert.deepEqual(errors, []);
    console.log('PASS: default-folder save, existing project preservation, new-paper placement, migration cancel/confirm, source and Git preserved, runtime clean');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
