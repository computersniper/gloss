// Acceptance with a mocked Capacitor bridge. Never calls a model or reads credentials.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright-core');

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.BROWSER_PATH || undefined });
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
    const errors = [], apiRequests = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => { if (request.url().includes('/chat/completions')) apiRequests.push(request.url()); });
    await page.addInitScript(() => {
      window.nativeCalls = [];
      window.nativeResolvers = [];
      window.nativeListeners = {};
      window.Capacitor = {
        isNativePlatform: () => true,
        nativePromise: (plugin, method, options) => {
          window.nativeCalls.push({ plugin, method, options });
          return new Promise(resolve => window.nativeResolvers.push(resolve));
        },
        Plugins: { App: { addListener: async (event, callback) => {
          window.nativeListeners[event] = callback;
          return { remove: async () => { if (window.nativeListeners[event] === callback) delete window.nativeListeners[event]; } };
        } } },
      };
    });
    await page.goto(process.argv[2] || 'http://127.0.0.1:5188/local/index.html?mode=local');
    await page.getByLabel('API Key', { exact: true }).fill('fake-native-test-key');
    const ask = async text => {
      await page.getByLabel('问题', { exact: true }).fill(text);
      await page.getByRole('button', { name: '发送', exact: true }).click();
    };
    await ask('Old native question');
    await page.waitForFunction(() => window.nativeCalls.length === 1);
    const call = await page.evaluate(() => window.nativeCalls[0]);
    assert.equal(call.plugin, 'CapacitorHttp');
    assert.equal(call.method, 'request');
    assert.equal(call.options.headers.Authorization, 'Bearer fake-native-test-key');
    assert.equal(call.options.url, 'https://api.openai.com/v1/chat/completions');
    assert.equal(call.options.data.stream, false);
    assert.equal(call.options.disableRedirects, true);
    await page.getByRole('button', { name: '停止', exact: true }).click();
    await page.getByRole('button', { name: '新对话', exact: true }).click();
    await ask('New native question');
    await page.waitForFunction(() => window.nativeCalls.length === 2);
    // A canceled request resolves while the new request is pending.
    await page.evaluate(() => window.nativeResolvers[0]({ status: 200, data: { choices: [{ message: { content: 'STALE OLD ANSWER' } }] } }));
    await page.waitForTimeout(100);
    assert.equal(await page.getByRole('button', { name: '停止', exact: true }).count(), 1);
    assert.equal(await page.getByRole('button', { name: '发送', exact: true }).isDisabled(), true);
    assert.equal(await page.getByText('STALE OLD ANSWER', { exact: true }).count(), 0);
    // Native implementations may return the JSON payload as a string.
    await page.evaluate(() => window.nativeResolvers[1]({ status: 200, data: JSON.stringify({ choices: [{ message: { content: 'CURRENT NATIVE ANSWER' } }] }) }));
    await page.getByText('CURRENT NATIVE ANSWER', { exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: '停止', exact: true }).count(), 0);
    assert.equal(apiRequests.length, 0, 'native mode must not fall back to browser model requests');
    assert.equal(await page.evaluate(() => localStorage.getItem('glossx.mobile.local.v1').includes('fake-native-test-key')), false);
    await page.waitForFunction(() => typeof window.nativeListeners.appUrlOpen === 'function');
    const link = 'glossx://pair?u=http%3A%2F%2F192.0.2.10%3A8010&t=fake-pair-code';
    // Stop at the shell hand-off: no pairing request to a real computer.
    await page.route('**/index.html', route => route.fulfill({ status: 200, contentType: 'text/html', body: '<title>Mock connection shell</title>' }));
    await page.evaluate(url => window.nativeListeners.appUrlOpen({ url }), link);
    await page.waitForURL(url => url.pathname === '/index.html' && url.hash.startsWith('#pair='));
    assert.equal(decodeURIComponent(new URL(page.url()).hash.slice(6)), link);
    assert.deepEqual(errors, []);
    console.log('PASS native HTTP contract, string JSON, cancellation identity, no browser API/key persistence, pairing deep-link hand-off');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
