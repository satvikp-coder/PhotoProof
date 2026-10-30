// Optional browser QA: run from a temporary npm tools directory, not an evidence root.
const { createRequire } = require('node:module');
const path = require('node:path');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const tools = createRequire(path.join(process.cwd(), 'package.json'));
const { chromium } = tools('playwright');
const { default: AxeBuilder } = tools('@axe-core/playwright');

async function main() {
  const base = process.argv[2] || 'http://127.0.0.1:8501';
  const browser = await chromium.launch({ headless: true });
  const directory = path.join(process.cwd(), 'screenshots');
  fs.mkdirSync(directory, { recursive: true });
  let checks = 0;
  try {
    for (const colorScheme of ['light', 'dark']) {
      for (const width of [1440, 768, 320]) {
        const context = await browser.newContext({ viewport: { width, height: 1000 }, colorScheme });
        for (const route of ['analyze', 'compare', 'history', 'diagnostics', 'about']) {
          const page = await context.newPage();
          const response = await page.goto(`${base}/${route}`);
          assert.equal(response.status(), 200);
          const result = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
          assert.deepEqual(result.violations.map(v => ({ id: v.id, nodes: v.nodes.map(n => n.target) })), [], `${route} ${colorScheme} ${width}`);
          assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'Page must not overflow horizontally');
          await page.keyboard.press('Tab');
          assert.equal(await page.locator(':focus').textContent(), 'Skip to main content');
          await page.keyboard.press('Enter');
          assert.equal(await page.locator(':focus').getAttribute('id'), 'main');
          await page.locator('nav a').first().focus();
          await page.keyboard.press('Tab');
          assert.equal(await page.locator(':focus').textContent().then(t => t.includes('Compare')), true);
          await page.screenshot({ path: path.join(directory, `${route}-${colorScheme}-${width}.png`), fullPage: true });
          checks += 1;
          await page.close();
        }
        await context.close();
      }
    }
    // The foundation must also render without JavaScript.
    const noScript = await browser.newContext({ javaScriptEnabled: false });
    const page = await noScript.newPage();
    for (const route of ['analyze', 'compare', 'history', 'diagnostics', 'about']) {
      assert.equal((await page.goto(`${base}/${route}`)).status(), 200);
      assert.equal(await page.locator('h1').count(), 1);
    }
    await noScript.close();
    process.stdout.write(`${checks} axe/layout/keyboard combinations passed; all pages render without JavaScript.\n`);
  } finally {
    await browser.close();
  }
}
main().catch(error => { process.stderr.write(`${error.stack}\n`); process.exitCode = 1; });
