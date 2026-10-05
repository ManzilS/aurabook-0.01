import assert from 'node:assert/strict'
import { chromium } from 'playwright'

const browser = await chromium.launch({ headless: true })
const base = process.env.TEST_BASE_URL || 'http://127.0.0.1:5174'
try {
  for (const width of [320, 390, 1440]) {
    for (const javaScriptEnabled of [true, false]) {
      const context = await browser.newContext({ javaScriptEnabled, viewport: { width, height: 900 } })
      const page = await context.newPage()
      await page.goto(base, { waitUntil: 'domcontentloaded' })
      await page.locator('h1').waitFor()
      if (javaScriptEnabled && width < 768) {
        const toggle = page.getByRole('button', { name: 'Toggle navigation' })
        await toggle.click()
        assert.equal(await toggle.getAttribute('aria-expanded'), 'true')
        await page.getByRole('navigation', { name: 'Mobile navigation' }).getByRole('link', { name: 'Demo', exact: true }).click()
        await page.getByRole('button', { name: 'Live Demo', exact: true }).click()
        await page.getByText('It does not stay only in your browser.', { exact: false }).waitFor()
        assert.equal(await toggle.getAttribute('aria-expanded'), 'false')
      }
      assert.equal(await page.locator('footer').count(), 1)
      assert.equal(await page.locator('a[href="https://github.com/ManzilS/full-book"]').count(), 0)
      await page.screenshot({ path: `smoke-navigation-${width}-${javaScriptEnabled}.png`, fullPage: true })
      await page.goto(`${base}/quickstart.html`, { waitUntil: 'domcontentloaded' })
      await page.getByRole('heading', { name: 'Get started with AuraBook' }).waitFor()
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      assert.ok((await page.locator('body').innerText()).includes('python -m pip install ./Client'))
      await context.close()
    }
  }
  console.log('PASS: mobile menu, docs, footer, private repo links, disclosures, JS/no-JS at 320/390/1440px')
} finally { await browser.close() }
