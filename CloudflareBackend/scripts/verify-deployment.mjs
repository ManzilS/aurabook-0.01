import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parseEnv } from 'node:util'
import { createHmac, randomUUID } from 'node:crypto'

// Credentials stay in memory. Never print request headers or bodies containing keys.
const config = { ...parseEnv(readFileSync(process.env.AURABOOK_VERIFY_ENV, 'utf8')), ...process.env }
const base = 'https://aurabook-api.manzilsapkota.workers.dev'
const db = config.AURABOOK_CLOUD_SUPABASE_URL
const dbKey = config.AURABOOK_CLOUD_SUPABASE_SERVICE_ROLE_KEY
const secret = config.AURABOOK_CLOUD_API_SHARED_SECRET
const providerKey = config.AURABOOK_CLOUD_OPENAI_API_KEY
const secrets = [dbKey, secret, providerKey].filter(Boolean)
const clean = (value) => secrets.reduce((text, key) => text.replaceAll(key, '[REDACTED]'), String(value))
const results = []

async function request(url, init = {}) {
  const response = await fetch(url, { ...init, signal: AbortSignal.timeout(90000) })
  const body = await response.json()
  if (!response.ok) throw new Error(`${new URL(url).pathname}: HTTP ${response.status}: ${JSON.stringify(body)}`)
  return body
}
function signed(body) {
  const timestamp = String(Math.floor(Date.now() / 1000))
  return { 'Content-Type': 'application/json', 'X-AuraBook-Timestamp': timestamp,
    'X-AuraBook-Signature': createHmac('sha256', secret).update(`${timestamp}.${body}`).digest('hex') }
}
async function check(name, run) {
  try { const detail = await run(); results.push({ name, pass: true, detail }) }
  catch (error) { results.push({ name, pass: false, error: clean(error.message) }) }
  console.log(JSON.stringify(results.at(-1)))
}
await check('Worker health', async () => (await request(`${base}/health`)).status)
await check('Worker snapshot matches Supabase', async () => {
  const rows = await request(`${db}/rest/v1/trajectory_notes?select=id`, { headers: { apikey: dbKey, Authorization: `Bearer ${dbKey}` } })
  const snapshot = await request(`${base}/v1/dashboard/snapshot`)
  assert.deepEqual(snapshot.notes.map(n => n.id).sort(), rows.map(n => n.id).sort())
  return `${rows.length} identical note IDs`
})
await check('Signed prefetch reaches Supabase', async () => {
  const rows = await request(`${db}/rest/v1/trajectories?select=cdc_hash,id&cdc_hash=not.is.null&limit=1`, { headers: { apikey: dbKey, Authorization: `Bearer ${dbKey}` } })
  assert.ok(rows.length)
  const body = JSON.stringify({ cdc_hash: rows[0].cdc_hash })
  const result = await request(`${base}/v1/prefetch`, { method: 'POST', headers: signed(body), body })
  assert.equal(result.found, true)
  assert.equal(result.match.trajectory_id, rows[0].id)
  return 'Existing trajectory retrieved with signed client request'
})
await check('Unauthorized writes rejected', async () => {
  for (const path of ['ingest','feedback','admin/prune']) {
    const response = await fetch(`${base}/v1/${path}`, { method: 'POST', body: '{}', headers: { 'Content-Type': 'application/json' } })
    assert.equal(response.status, 401)
  }
  return 'ingest, feedback, prune: 401'
})
await check('Vector RPC uses the stored embedding', async () => {
  const headers = { apikey: dbKey, Authorization: `Bearer ${dbKey}`, 'Content-Type': 'application/json' }
  const rows = await request(`${db}/rest/v1/trajectory_notes?select=id,embedding&limit=1`, { headers })
  const matches = await request(`${db}/rest/v1/rpc/match_notes`, { method: 'POST', headers, body: JSON.stringify({ query_embedding: rows[0].embedding, match_threshold: 0.99, result_limit: 1 }) })
  assert.equal(matches[0]?.note_id, rows[0].id)
  return 'Exact embedding retrieved its own note'
})
await check('Live provider model discovery', async () => {
  const result = await request(`${base}/v1/demo/models`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider: 'openai', api_key: providerKey }) })
  assert.equal(result.valid, true, result.error || 'Key validation failed')
  assert.ok(result.models.includes('gpt-4o-mini'))
  return `${result.models.length} available models; gpt-4o-mini available`
})
if (process.argv.includes('--live-model')) {
  await check('Two real model responses through Worker', async () => {
    const result = await request(`${base}/v1/demo/compare`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ provider: 'openai', api_key: providerKey, model: 'gpt-4o-mini', prompt: 'Return only a fenced Python code block that prints AURABOOK_VERIFY_OK. No other code or explanation.' }) })
    for (const events of [result.left_events, result.right_events]) assert.ok(events.some(e => e.kind === 'success' && e.text.includes('AURABOOK_VERIFY_OK')))
    assert.ok(result.total_tokens > 0)
    return { model: result.model_used, calls: result.provider_call_count, tokens: result.total_tokens }
  })
}
if (process.argv.includes('--write-test')) {
  await check('Signed ingest, deduplication, prefetch, search, feedback, cleanup', async () => {
    const id = randomUUID()
    const marker = `deployment-verification-${id}`
    const headers = { apikey: dbKey, Authorization: `Bearer ${dbKey}`, 'Content-Type': 'application/json' }
    const post = (path, data) => {
      const body = JSON.stringify(data)
      return request(`${base}/v1/${path}`, { method: 'POST', headers: signed(body), body })
    }
    const payload = {
      trajectory_id: id, source: 'local_client', client_id: marker, cdc_hash: marker,
      original_prompt: `Verification ${id}: print the quoted string AURABOOK_VERIFY_OK in Python.`,
      broken_code: 'print(AURABOOK_VERIFY_OK)',
      error_log: `NameError: name AURABOOK_VERIFY_OK is not defined. Verification ${id}.`,
      final_fix: 'print("AURABOOK_VERIFY_OK")',
      metadata: { deployment_verification: marker },
    }
    try {
      const created = await post('ingest', payload)
      assert.equal(created.trajectory_id, id)
      assert.equal(created.created, true)
      assert.ok(created.stored_nodes > 0 && created.stored_edges > 0)
      const duplicate = await post('ingest', payload)
      assert.equal(duplicate.created, false)
      const prefetched = await post('prefetch', { cdc_hash: marker })
      assert.equal(prefetched.found, true)
      assert.ok(prefetched.match.note_text)
      const searchable = [payload.original_prompt, payload.broken_code, payload.error_log, payload.final_fix].join('\n\n')
      const search = await post('search', { prompt: searchable, match_threshold: 0.99, limit: 1 })
      assert.equal(search.matches[0]?.trajectory_id, id)
      const feedback = await post('feedback', { note_id: prefetched.match.note_id, trajectory_id: id, feedback: 'helpful' })
      assert.equal(feedback.accepted, true)
      const rows = await request(`${db}/rest/v1/trajectory_notes?trajectory_id=eq.${id}&select=helpful_count,embedding`, { headers })
      assert.equal(rows[0].helpful_count, 1)
      assert.equal(JSON.parse(rows[0].embedding).length, 1536)
      const snapshot = await request(`${base}/v1/dashboard/snapshot`)
      assert.ok(snapshot.notes.some(n => n.trajectory_id === id))
    } finally {
      // Delete only this test's freshly generated identity, never pre-existing records.
      const response = await fetch(`${db}/rest/v1/trajectories?id=eq.${id}&client_id=eq.${marker}`, { method: 'DELETE', headers })
      assert.ok(response.ok, `Cleanup failed for verification trajectory ${id}`)
      for (const table of ['trajectories', 'trajectory_notes', 'trajectory_nodes', 'trajectory_edges']) {
        const column = table === 'trajectories' ? 'id' : 'trajectory_id'
        const remaining = await request(`${db}/rest/v1/${table}?${column}=eq.${id}&select=id`, { headers })
        assert.equal(remaining.length, 0, `Test rows remain in ${table}`)
      }
    }
    return 'Write/read pipeline passed; all temporary rows removed'
  })
}
if (results.some(r => !r.pass)) process.exitCode = 1
