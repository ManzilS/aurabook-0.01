import assert from 'node:assert/strict'
import { createHmac } from 'node:crypto'
import test from 'node:test'
import worker from '../src/index.ts'

test('prefetch returns a note for object and array relationship shapes', async () => {
  const env = { AURABOOK_CLOUD_API_SHARED_SECRET: 'test-secret', AURABOOK_CLOUD_SUPABASE_URL: 'https://database.example', AURABOOK_CLOUD_SUPABASE_SERVICE_ROLE_KEY: 'test-only' }
  const originalFetch = globalThis.fetch
  const note = { id: 'note-id', note_text: 'System Note: use quoted strings.', source_model: 'test' }
  try {
    for (const relation of [note, [note]]) {
      globalThis.fetch = async () => Response.json([{ id: 'trajectory-id', cdc_hash: 'test-hash', final_fix: 'print("hi")', trajectory_notes: relation }])
      const body = JSON.stringify({ cdc_hash: 'test-hash' })
      const timestamp = String(Math.floor(Date.now() / 1000))
      const response = await worker.fetch(new Request('https://worker.example/v1/prefetch', {
        method: 'POST', body, headers: {
          'X-AuraBook-Timestamp': timestamp,
          'X-AuraBook-Signature': createHmac('sha256', env.AURABOOK_CLOUD_API_SHARED_SECRET).update(`${timestamp}.${body}`).digest('hex'),
        },
      }), env)
      assert.equal(response.status, 200)
      const result = await response.json()
      assert.equal(result.match.note_text, note.note_text)
      assert.equal(result.match.note_id, note.id)
    }
  } finally { globalThis.fetch = originalFetch }
})

test('CORS grants access only to configured origins', async () => {
  const env = { AURABOOK_ALLOWED_ORIGINS: 'https://aurabooks.vercel.app' }
  for (const origin of ['https://aurabooks.vercel.app', 'https://unrelated.vercel.app']) {
    const response = await worker.fetch(new Request('https://worker.example/health', { headers: { origin } }), env)
    assert.equal(response.headers.get('access-control-allow-origin'), 'https://aurabooks.vercel.app')
    assert.equal(response.headers.get('vary'), 'Origin')
  }
})

test('private endpoints require configuration and authenticate cached responses', async () => {
  const env = {
    AURABOOK_CLOUD_API_SHARED_SECRET: 'test-secret',
    AURABOOK_CLOUD_SUPABASE_URL: 'https://database.example',
    AURABOOK_CLOUD_SUPABASE_SERVICE_ROLE_KEY: 'test-only',
  }
  const body = JSON.stringify({ cdc_hash: 'test-hash-123' })
  const request = (signed) => {
    const headers = { 'Content-Type': 'application/json', 'X-AuraBook-Idempotency-Key': 'test-cache' }
    if (signed) {
      const timestamp = String(Math.floor(Date.now() / 1000))
      headers['X-AuraBook-Timestamp'] = timestamp
      headers['X-AuraBook-Signature'] = createHmac('sha256', env.AURABOOK_CLOUD_API_SHARED_SECRET)
        .update(`${timestamp}.${body}`).digest('hex')
    }
    return new Request('https://worker.example/v1/prefetch', { method: 'POST', headers, body })
  }
  assert.equal((await worker.fetch(request(false), {})).status, 401)
  assert.equal((await worker.fetch(request(false), env)).status, 401)
  const originalFetch = globalThis.fetch
  globalThis.fetch = async () => Response.json([])
  try {
    assert.equal((await worker.fetch(request(true), env)).status, 200)
    assert.equal((await worker.fetch(request(false), env)).status, 401)
    assert.equal((await worker.fetch(request(true), env)).status, 200)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('DeepSeek validation uses its endpoint and warms model names without validating another key', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = async (url) => {
    assert.equal(url, 'https://api.deepseek.com/v1/models')
    return Response.json({ data: [{ id: 'deepseek-chat' }] })
  }
  try {
    const response = await worker.fetch(new Request('https://worker.example/v1/demo/models', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider: 'deepseek', api_key: 'test-only' }),
    }), {})
    assert.equal((await response.json()).valid, true)
    const warm = await worker.fetch(new Request('https://worker.example/v1/demo/models/warm?provider=deepseek'), {})
    const data = await warm.json()
    assert.equal(data.valid, false)
    assert.equal(data.cache_status, 'warm')
    assert.deepEqual(data.models, ['deepseek-chat'])
    assert.ok(!JSON.stringify(data).includes('test-only'))
  } finally {
    globalThis.fetch = originalFetch
  }
})
