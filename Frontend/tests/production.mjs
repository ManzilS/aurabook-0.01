import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parseEnv } from 'node:util'
import { chromium } from 'playwright'

// Intentionally no traces, screenshots, or request-body logging with real credentials.
const config = parseEnv(readFileSync(process.env.AURABOOK_VERIFY_ENV, 'utf8'))
const key = config.AURABOOK_CLOUD_OPENAI_API_KEY
const browser = await chromium.launch({ headless: true })
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
  const errors = []
  let snapshots = 0
  page.on('pageerror', error => errors.push(error.message))
  page.on('websocket', socket => socket.on('framereceived', ({ payload }) => {
    try { if (JSON.parse(String(payload)).type === 'snapshot') snapshots++ } catch { /* Ignore non-JSON control frames. */ }
  }))
  await page.goto('https://aurabooks.vercel.app/dashboard', { waitUntil: 'domcontentloaded' })
  await page.getByText('System Note:', { exact: false }).first().waitFor()
  const deadline = Date.now() + 25000
  while (snapshots < 2 && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 500))
  assert.ok(snapshots >= 2, 'Expected initial and subsequent WebSocket snapshots')
  console.log('PASS: browser dashboard receives real notes and repeated WebSocket snapshots')
  await page.goto('https://aurabooks.vercel.app/demo', { waitUntil: 'domcontentloaded' })
  await page.getByRole('button', { name: 'Live Demo', exact: true }).click()
  await page.locator('input[type=password]').fill(key)
  await page.getByText(/API key works/).waitFor({ timeout: 30000 })
  await page.getByRole('combobox', { name: /^Model/ }).selectOption('gpt-4o-mini')
  await page.getByPlaceholder('Ask anything you want both live agents to answer.').fill('Return only a fenced Python code block that prints AURABOOK_VERIFY_OK. No other code or explanation.')
  await page.getByRole('button', { name: 'Run Two Live Calls', exact: true }).click()
  const outputs = page.locator('.typewriter').filter({ hasText: /stdout:\s*AURABOOK_VERIFY_OK/ })
  await outputs.nth(1).waitFor({ timeout: 180000 })
  assert.equal(await outputs.count(), 2)
  assert.deepEqual(errors, [])
  console.log('PASS: real key validation, model selection, two live provider responses, and two Pyodide outputs')
} catch (error) {
  throw new Error(String(error.message).replaceAll(key, '[REDACTED]'))
} finally { await browser.close() }
