// Run with an existing Playwright installation (no project dependency added).
// PLAYWRIGHT_MODULE=<path to playwright-core> BROWSER_PATH=<Chromium executable>
// node scripts/dev/ui_scrollbar.cjs http://127.0.0.1:8792
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright-core');
const base = process.argv[2] || 'http://127.0.0.1:8792';
const activities = ['Library', 'Threads', 'Workspace', 'Source control', 'Projects', 'Search (Scholar)', 'Capabilities & Plugins', 'Settings'];
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.BROWSER_PATH || undefined });
  try {
    const context = await browser.newContext();
    await context.addInitScript(() => {
      localStorage.setItem('gloss.mode', 'glossx');
      localStorage.setItem('gloss.uiLang', 'en');
      localStorage.setItem('gloss.layout', JSON.stringify({ sideOpen: true, activity: 'library', aiOpen: true, bottomOpen: true, sideWidth: 300, aiWidth: 400, bottomHeight: 200 }));
    });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(base);
    await page.locator('.wb').waitFor();
    // Disable the document overflow guard during checks: hiding overflow alone
    // must not make a broken flex layout pass the acceptance test.
    const guard = await page.addStyleTag({ content: 'html, body { overflow: visible !important; }' });
    for (const [width, height] of [[1440, 900], [1280, 720], [1000, 700]]) {
      await page.setViewportSize({ width, height });
      for (const name of activities) {
        await page.getByRole('button', { name, exact: true }).first().click();
        await page.waitForTimeout(150);
        const dimensions = await page.evaluate(() => {
          const root = document.scrollingElement;
          return {
            scrollHeight: root.scrollHeight, clientHeight: root.clientHeight,
            scrollWidth: root.scrollWidth, clientWidth: root.clientWidth,
            shellBottom: document.querySelector('.wb').getBoundingClientRect().bottom,
            centerBottom: document.querySelector('.wb-center').getBoundingClientRect().bottom,
          };
        });
        console.log(JSON.stringify({ width, height, activity: name, ...dimensions }));
        assert.equal(dimensions.scrollHeight, dimensions.clientHeight, `${width}x${height}: ${name} vertical overflow`);
        assert.equal(dimensions.scrollWidth, dimensions.clientWidth, `${width}x${height}: ${name} horizontal overflow`);
        assert.ok(dimensions.shellBottom <= height && dimensions.centerBottom <= height, 'Flex ancestors must remain inside the viewport');
      }
      // Force content beyond the viewport: it must scroll inside its panel.
      await page.evaluate(() => {
        const panel = document.querySelector('.wb-settings-page > .panel-body');
        const stress = document.createElement('div');
        stress.dataset.scrollStress = 'true';
        stress.style.height = '2000px';
        stress.style.flexShrink = '0';
        panel.append(stress);
      });
      assert.ok(await page.evaluate(() => {
        const panel = document.querySelector('.wb-settings-page > .panel-body');
        panel.scrollTop = 100;
        const root = document.scrollingElement;
        return panel.scrollTop > 0 && root.scrollHeight === root.clientHeight;
      }), 'Settings must scroll internally, not clip its contents');
      await page.evaluate(() => document.querySelector('[data-scroll-stress]').remove());
    }
    await guard.evaluate(el => el.remove());
    await page.keyboard.press('Control+Shift+M');
    await page.locator('.app.mode-gloss').waitFor();
    assert.equal(await page.evaluate(() => getComputedStyle(document.documentElement).overflowY), 'visible', 'Classic mode must restore document scrolling');
    await page.keyboard.press('Control+Shift+M');
    await page.locator('.wb').waitFor();
    assert.deepEqual(errors, [], 'No frontend runtime errors');
    console.log('PASS: 24 viewport/activity checks, internal settings scroll, and mode switching');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
