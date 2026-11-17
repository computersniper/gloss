// Browser check of the two branch actions never clicked before: merge and delete.
const { chromium } = require('C:/Users/caijiechao/AppData/Local/Temp/claude/D--study-vibe-coding-glossX/ee2824b5-e972-4673-8262-601f3d47c73b/scratchpad/pw/node_modules/playwright-core');
const base = process.argv[2];
const out = process.argv[3];
(async () => {
  const browser = await chromium.launch({ executablePath: 'C:/Users/caijiechao/AppData/Local/ms-playwright/chromium-1243/chrome-win64/chrome.exe', args: ['--no-proxy-server'] });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const req = ctx.request;
  const api = async (method, p, data) => { const r = await req.fetch(base + p, { method, data }); if (!r.ok()) throw Error(p + ' ' + r.status() + ' ' + await r.text()); return r.json(); };
  const project = await api('POST', '/api/agent/projects', { name: 'Merge and delete check' });
  const pid = project.id, ws = '/api/workspace/' + pid;
  await api('GET', ws + '/vcs/status');
  // main: base file; feature: one more commit (fast-forward merge); clash: conflicting edit
  await api('PUT', ws + '/file?path=notes/a.md', { content: 'line one\n' });
  await api('POST', ws + '/vcs/commit', { message: 'base' });
  await api('POST', ws + '/vcs/branches', { name: 'feature' });
  await api('POST', ws + '/vcs/checkout', { branch: 'feature' });
  await api('PUT', ws + '/file?path=notes/b.md', { content: 'from feature\n' });
  await api('POST', ws + '/vcs/commit', { message: 'feature work' });
  await api('POST', ws + '/vcs/checkout', { branch: 'main' });
  await api('POST', ws + '/vcs/branches', { name: 'clash' });
  await api('POST', ws + '/vcs/checkout', { branch: 'clash' });
  await api('PUT', ws + '/file?path=notes/a.md', { content: 'clash version\n' });
  await api('POST', ws + '/vcs/commit', { message: 'clash edit' });
  await api('POST', ws + '/vcs/checkout', { branch: 'main' });
  await api('PUT', ws + '/file?path=notes/a.md', { content: 'main version\n' });
  await api('POST', ws + '/vcs/commit', { message: 'main edit' });

  await ctx.addInitScript(p => {
    localStorage.setItem('gloss.mode', 'glossx'); localStorage.setItem('gloss.theme', 'midnight'); localStorage.setItem('gloss.uiLang', 'en');
    localStorage.setItem('gloss.agentProject', p);
    localStorage.setItem('gloss.layout', JSON.stringify({ activity: 'scm', sideOpen: true, aiOpen: false, bottomOpen: false, sideWidth: 340, aiWidth: 450 }));
  }, pid);
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.goto(base);
  const btn = page.locator('.gx-branch-btn').first();
  await btn.waitFor();
  const openMenu = async () => { if (!(await page.locator('.gx-branches').isVisible().catch(() => false))) await btn.click(); await page.locator('.gx-branch-list').waitFor(); };
  const confirmIfAsked = async () => { const d = page.getByRole('alertdialog'); if (await d.isVisible().catch(() => false)) { await page.screenshot({ path: out + '/merge-delete-confirm.png' }); const ok = d.getByRole('button').last(); await ok.click(); return true; } return false; };

  // 1. fast-forward-able merge of feature into main (main has diverged, so a merge commit)
  await openMenu();
  await page.screenshot({ path: out + '/branches-menu.png' });
  await page.locator('button[title="Merge feature into main"]').click();
  await page.waitForTimeout(800); await confirmIfAsked(); await page.waitForTimeout(1500);
  const tree = await api('GET', ws + '/tree');
  const names = JSON.stringify(tree.entries);
  let st = await api('GET', ws + '/vcs/status');
  if (st.branch !== 'main' || !st.clean) throw Error('after merge: ' + JSON.stringify({ branch: st.branch, clean: st.clean }));
  const b = await req.fetch(base + ws + '/file?path=notes/b.md');
  if (!b.ok()) throw Error('merged file notes/b.md missing on main ' + names);
  console.log('PASS merge feature into main from the UI; merged file present, tree clean');
  await page.screenshot({ path: out + '/after-merge.png' });

  // 2. conflicting merge: must be reported and aborted, repo not left mid-merge
  await openMenu();
  await page.locator('button[title="Merge clash into main"]').click();
  await page.waitForTimeout(800); await confirmIfAsked(); await page.waitForTimeout(1500);
  await page.screenshot({ path: out + '/merge-conflict.png' });
  st = await api('GET', ws + '/vcs/status');
  if (st.branch !== 'main' || !st.clean) throw Error('conflict left repo dirty: ' + JSON.stringify(st.changes));
  const a = await (await req.fetch(base + ws + '/file?path=notes/a.md')).json();
  if (!String(a.content).includes('main version')) throw Error('conflict changed main content');
  const visible = await page.evaluate(() => document.body.innerText);
  if (!/conflict/i.test(visible)) throw Error('conflict was not reported in the UI');
  console.log('PASS conflicting merge is reported in the UI and aborted; main unchanged and clean');
  await page.keyboard.press('Escape'); await page.waitForTimeout(300);
  await confirmIfAsked();

  // 3. delete the merged branch
  await openMenu();
  await page.locator('button[title="Delete branch feature"]').click();
  await page.waitForTimeout(600); await confirmIfAsked(); await page.waitForTimeout(1200);
  st = await api('GET', ws + '/vcs/status');
  if (st.branches.some(x => x.name === 'feature')) throw Error('feature branch still exists');
  if (!st.branches.some(x => x.name === 'main' && x.current)) throw Error('main is not current after delete');
  console.log('PASS delete branch from the UI; branches now: ' + st.branches.map(x => x.name).join(', '));
  await openMenu();
  await page.screenshot({ path: out + '/after-delete.png' });

  console.log(JSON.stringify({ pageErrors: errors }));
  if (errors.length) process.exitCode = 1;
  await browser.close();
})().catch(e => { console.error(String(e && e.stack || e)); process.exit(1); });
