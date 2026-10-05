import assert from 'node:assert/strict'
import { chromium } from 'playwright'

const base = process.env.TEST_BASE_URL || 'http://127.0.0.1:5174'
const browser = await chromium.launch({ headless: true })
try {
  for (const javaScriptEnabled of [true, false]) {
    for (const width of [390, 1440]) {
      const context = await browser.newContext({ javaScriptEnabled, viewport: { width, height: 900 } })
      const page = await context.newPage()
      const errors = []
      page.on('pageerror', (error) => errors.push(error.message))
      await page.route('**/v1/dashboard/**', (route) => route.abort())
      await page.goto(base)
      await page.locator('h1').first().waitFor()
      assert.ok(await page.locator('body').innerText().then((text) => text.includes('AuraBook')))
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'page overflow')
      await page.screenshot({ path: `smoke-${javaScriptEnabled ? 'js' : 'nojs'}-${width}.png`, fullPage: true })
      if (javaScriptEnabled) {
        await page.goto(`${base}/dashboard`)
        await page.getByRole('alert').waitFor()
        assert.match(await page.getByRole('alert').innerText(), /Cannot reach/)
        assert.equal(await page.getByText('System Note:', { exact: false }).count(), 0)
        await page.goto(`${base}/demo`)
        await page.getByRole('button', { name: 'Live Demo', exact: true }).click()
        await page.getByLabel('Provider', { exact: true }).selectOption('deepseek')
        assert.equal(await page.getByLabel('Provider', { exact: true }).inputValue(), 'deepseek')
      }
      assert.deepEqual(errors, [])
      await context.close()
    }
  }
  const page = await browser.newPage()
  await page.goto(base)
  const results = await page.evaluate(async () => {
    const { runPythonSnippet } = await import('/src/services/pyodideRunner.ts')
    return [
      await runPythonSnippet('print("hi")'),
      await runPythonSnippet('raise ValueError("visible failure")'),
      await runPythonSnippet('while True: pass', 500),
    ]
  })
  assert.equal(results[0].stdout.trim(), 'hi')
  assert.match(results[1].error, /ValueError: visible failure/)
  assert.match(results[2].error, /time limit/)
  await page.route('**/v1/demo/compare', async (route) => {
    assert.equal(route.request().postDataJSON().provider, 'deepseek')
    const event = { at: 0, kind: 'success', text: '```python\nprint("comparison hi")\n```', token_count: 12, status_label: 'Provider complete' }
    await route.fulfill({ json: {
      injected_note: null, left_events: [event], right_events: [event],
      baseline_tokens: 12, aurabook_tokens: 12, total_tokens: 24,
      provider_used: 'deepseek', model_used: 'deepseek-chat',
      retrieval_provider_call_count: 0, provider_call_count: 2,
    } })
  })
  await page.goto(`${base}/demo`)
  await page.getByRole('button', { name: 'Live Demo', exact: true }).click()
  await page.route('**/v1/demo/models', (route) => route.fulfill({ json: {
    provider: 'deepseek', valid: true, models: ['deepseek-chat'], default_model: 'deepseek-chat', cache_status: 'live', error: null,
  } }))
  await page.getByLabel('Provider', { exact: true }).selectOption('deepseek')
  await page.locator('input[type=password]').fill('test-only-placeholder-key')
  await page.getByText(/API key works/).waitFor()
  await page.getByRole('button', { name: 'Run Two Live Calls', exact: true }).click()
  const outputs = page.locator('.typewriter').filter({ hasText: /stdout:\s*comparison hi/ })
  await outputs.nth(1).waitFor({ timeout: 180000 })
  assert.equal(await outputs.count(), 2)
  console.log('PASS: desktop/mobile, JS/no-JS, offline dashboard, provider selection, Python output/errors/timeout')
} finally {
  await browser.close()
}
