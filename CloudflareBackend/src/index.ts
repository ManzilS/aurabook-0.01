interface Env {
  AURABOOK_ALLOWED_ORIGINS?: string
  AURABOOK_CLOUD_SUPABASE_URL?: string
  AURABOOK_CLOUD_SUPABASE_SERVICE_ROLE_KEY?: string
  AURABOOK_CLOUD_OPENAI_API_KEY?: string
  AURABOOK_CLOUD_API_SHARED_SECRET?: string
  AURABOOK_CLOUD_API_SIGNATURE_MAX_AGE_S?: string
  AURABOOK_CLOUD_SEARCH_MATCH_THRESHOLD?: string
  AURABOOK_CLOUD_RETENTION_DAYS?: string
}

interface DemoEvent {
  at: number
  kind: 'agent' | 'note' | 'success' | 'error'
  text: string
  token_count: number
  status_label: string
}

interface ProviderResult {
  text: string
  totalTokens: number
}

interface NoteRow {
  id: string
  trajectory_id: string
  note_text: string
  source_model: string
  metadata?: Record<string, unknown>
  created_at: string
  updated_at?: string
  helpful_count?: number
  unhelpful_count?: number
  suppress_count?: number
  match_count?: number
  trajectories?: {
    original_prompt?: string
    final_fix?: string
    error_log?: string
    ai_reflection?: string
    history?: unknown[]
    metadata?: Record<string, unknown>
    note_model?: string
    created_at?: string
    updated_at?: string
  }
}

interface DashboardNote {
  id: string
  error_cluster_id: string
  trajectory_id: string
  problem_tag: string
  summary: string
  diagnosis_summary: string
  full_diagnosis: string
  model: string
  created_at: string
  velocity: 'stable'
  status: 'Stable'
  score: number
  unsolved: boolean
  language: string
  problem_description: string
  solution_text: string
  environment: {
    os: string
    language_version: string
    libraries: string[]
  }
  usage_count: number
  attempted_models: string[]
  unsolved_attempt_count: number
  failed_model_count: number
  attempt_count: number
  attempts: unknown[]
  rating_history: Array<{ timestamp: string; score: number }>
  comments: unknown[]
  community_context: string
}

interface ModelListResponse {
  provider: string
  valid: boolean
  models: string[]
  default_model: string
  cache_status: string
  error: string | null
}

interface IngestPayload {
  trajectory_id?: string
  id?: string
  source?: 'local_client' | 'swarm' | 'manual' | 'worker'
  client_id?: string | null
  cdc_hash?: string | null
  original_goal?: string | null
  original_prompt?: string
  trigger_prompt?: string
  broken_code?: string | null
  attempt_code?: string | null
  attempt?: string | null
  error_log?: string
  previous_error_log?: string
  ai_reflection?: string | null
  final_fix?: string
  final_code?: string
  fix?: string
  history?: Array<Record<string, unknown>>
  metadata?: Record<string, unknown>
}

interface StoredTrajectoryRow {
  id: string
  source: string
  client_id?: string | null
  cdc_hash?: string | null
  trajectory_fingerprint?: string | null
  original_goal?: string | null
  original_prompt: string
  broken_code?: string | null
  error_log: string
  ai_reflection?: string | null
  final_fix: string
  history?: Array<Record<string, unknown>>
  metadata?: Record<string, unknown>
  processed?: boolean
  note_model?: string | null
  note_error?: string | null
  processed_at?: string | null
  created_at?: string
  updated_at?: string
}

interface NormalizedTrajectory {
  trajectory_id: string
  source: string
  client_id: string | null
  cdc_hash: string | null
  original_goal: string | null
  original_prompt: string
  broken_code: string | null
  error_log: string
  ai_reflection: string | null
  final_fix: string
  history: Array<Record<string, unknown>>
  metadata: Record<string, unknown>
}

interface SearchPayload {
  prompt?: string | null
  error_log?: string | null
  limit?: number
  match_threshold?: number | null
}

interface FeedbackPayload {
  note_id?: string | null
  trajectory_id?: string | null
  client_id?: string
  feedback?: 'helpful' | 'wrong' | 'too_aggressive'
  repository?: string | null
  metadata?: Record<string, unknown>
}

interface IdempotencyEntry {
  expiresAt: number
  payload: unknown
}

const jsonHeaders = { 'content-type': 'application/json; charset=utf-8' }
const IDEMPOTENCY_TTL_MS = 24 * 60 * 60 * 1000
const idempotencyCache = new Map<string, IdempotencyEntry>()

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const cors = corsHeaders(request, env)
    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: cors })
    }

    const url = new URL(request.url)
    try {
      if (url.pathname === '/health') {
        const payload = { status: 'ok', runtime: 'cloudflare-worker' }
        return wantsHtml(request) ? html(renderPage('AuraBook API Health', `<p>Status: ${payload.status}</p><p>Runtime: ${payload.runtime}</p>`), cors) : json(payload, cors)
      }
      if (url.pathname === '/v1/dashboard/snapshot' && request.method === 'GET') {
        const payload = await dashboardSnapshot(env)
        return wantsHtml(request) ? html(renderDashboard(payload), cors) : json(payload, cors)
      }
      if (url.pathname === '/v1/dashboard/ws') {
        return dashboardWebsocket(request, env)
      }
      if (url.pathname === '/v1/demo/models/warm' && request.method === 'GET') {
        const payload = warmModels(url.searchParams.get('provider') ?? 'openai')
        return wantsHtml(request) ? html(renderModels(payload), cors) : json(payload, cors)
      }
      if (url.pathname === '/v1/demo/models' && request.method === 'POST') {
        const body = await parsePublicBody<{ api_key?: string; provider?: string }>(request)
        const payload = await listModelsForKey(body.api_key ?? '', body.provider)
        return wantsHtml(request) ? html(renderModels(payload), cors) : json(payload, cors)
      }
      if (url.pathname === '/v1/demo/compare' && request.method === 'POST') {
        const body = await parsePublicBody<{ prompt?: string; api_key?: string; model?: string }>(request)
        const payload = await liveCompare(body.prompt ?? '', body.api_key ?? '', body.model, env)
        return wantsHtml(request) ? html(renderCompare(payload), cors) : json(payload, cors)
      }
      if (url.pathname === '/v1/ingest' && request.method === 'POST') {
        return privateJson(request, env, cors, (body) => ingestTrajectory(parseJson<IngestPayload>(body), env))
      }
      if (url.pathname === '/v1/prefetch' && request.method === 'POST') {
        return privateJson(request, env, cors, (body) => prefetchTrajectory(parseJson<{ cdc_hash?: string }>(body), env))
      }
      if (url.pathname === '/v1/search' && request.method === 'POST') {
        return privateJson(request, env, cors, (body) => searchNotes(parseJson<SearchPayload>(body), env))
      }
      if (url.pathname === '/v1/feedback' && request.method === 'POST') {
        return privateJson(request, env, cors, (body) => submitFeedback(parseJson<FeedbackPayload>(body), env))
      }
      if (url.pathname === '/v1/admin/prune' && request.method === 'POST') {
        return privateJson(request, env, cors, () => pruneOldRows(env))
      }

      return json({ error: 'Not found' }, cors, 404)
    } catch (error) {
      const message = errorMessage(error)
      return wantsHtml(request)
        ? html(renderPage('AuraBook Request Failed', `<section class="panel"><h2>Request failed</h2><pre>${escapeHtml(message)}</pre><p><a href="https://aurabooks.vercel.app/#demo">Back to demo</a></p></section>`), cors, 500)
        : json({ error: message }, cors, 500)
    }
  },
}

function dashboardWebsocket(request: Request, env: Env): Response {
  if (request.headers.get('upgrade') !== 'websocket') {
    return new Response('Expected websocket upgrade', { status: 426 })
  }

  const pair = new WebSocketPair()
  const client = pair[0]
  const server = pair[1]

  server.accept()
  let active = true

  server.addEventListener('close', () => {
    active = false
  })
  server.addEventListener('error', () => {
    active = false
  })

  const sendSnapshot = async () => {
    while (active) {
      try {
        server.send(
          JSON.stringify({
            type: 'snapshot',
            snapshot: await dashboardSnapshot(env),
          }),
        )
      } catch {
        active = false
      }
      await sleep(8000)
    }
  }

  void sendSnapshot()

  return new Response(null, {
    status: 101,
    webSocket: client,
  })
}

function corsHeaders(request: Request, env: Env): HeadersInit {
  const origin = request.headers.get('origin') ?? ''
  const allowed = (env.AURABOOK_ALLOWED_ORIGINS ?? '')
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
  const allowOrigin =
    allowed.includes(origin) || origin.endsWith('.vercel.app') ? origin : allowed[0] ?? '*'

  return {
    'access-control-allow-origin': allowOrigin,
    'access-control-allow-methods': 'GET,POST,OPTIONS',
    'access-control-allow-headers':
      'content-type,authorization,x-aurabook-timestamp,x-aurabook-signature,x-aurabook-idempotency-key',
    'access-control-max-age': '86400',
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function json(payload: unknown, cors: HeadersInit, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { ...jsonHeaders, ...cors },
  })
}

function html(markup: string, cors: HeadersInit, status = 200): Response {
  return new Response(markup, {
    status,
    headers: { 'content-type': 'text/html; charset=utf-8', ...cors },
  })
}

function wantsHtml(request: Request): boolean {
  return (request.headers.get('accept') ?? '').includes('text/html')
}

async function parsePublicBody<T extends Record<string, unknown>>(request: Request): Promise<T> {
  const contentType = request.headers.get('content-type') ?? ''
  if (contentType.includes('application/x-www-form-urlencoded') || contentType.includes('multipart/form-data')) {
    const form = await request.formData()
    const body: Record<string, unknown> = {}
    for (const [key, value] of form.entries()) {
      body[key] = String(value)
    }
    return body as T
  }
  return request.json<T>()
}

function renderPage(title: string, body: string): string {
  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>${escapeHtml(title)}</title>
  <style>
    body{margin:0;background:radial-gradient(circle at top left,rgba(0,212,255,.08),transparent 34%),radial-gradient(circle at top right,rgba(0,255,136,.05),transparent 26%),#0a0a0a;color:#fff;font:16px/1.55 Inter,system-ui,-apple-system,Segoe UI,sans-serif}
    main{width:min(1180px,calc(100% - 32px));margin:0 auto;padding:36px 0 64px}
    a{color:#00d4ff}.panel{border:1px solid rgba(255,255,255,.08);background:linear-gradient(180deg,rgba(17,17,17,.96),rgba(10,10,10,.92));border-radius:28px;padding:24px;margin:16px 0;box-shadow:0 20px 80px rgba(0,0,0,.45)}
    pre{white-space:pre-wrap;background:#030303;border:1px solid rgba(255,255,255,.08);border-radius:18px;padding:16px;overflow:auto;color:#f5f5f5;font-family:"JetBrains Mono",monospace}
    .muted{color:#888}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:18px}.pill{display:inline-flex;border:1px solid rgba(0,212,255,.2);background:rgba(0,212,255,.1);color:#00d4ff;border-radius:999px;padding:8px 14px;font-size:13px}.success{color:#00ff88}.error{color:#ff6b35}
  </style>
</head>
<body>
  <main>
    <p><a href="https://aurabooks.vercel.app/">AuraBook</a> / <a href="https://aurabooks.vercel.app/llms.txt">Agent guide</a></p>
    <h1>${escapeHtml(title)}</h1>
    ${body}
  </main>
</body>
</html>`
}

function renderDashboard(payload: unknown): string {
  const snapshot = payload as { notes?: Array<Record<string, unknown>>; stats?: { total_notes?: number; active_agents?: number; problems_solved_today?: number } }
  const notes = snapshot.notes ?? []
  const stats = snapshot.stats ?? {}
  const noteHtml = notes.length
    ? notes.map((note) => `<article class="panel"><h2>${escapeHtml(String(note.problem_tag ?? 'Knowledge note'))}</h2><p>${escapeHtml(String(note.summary ?? note.community_context ?? ''))}</p><p class="muted">Score ${escapeHtml(String(note.score ?? 0))} · ${escapeHtml(String(note.created_at ?? ''))}</p></article>`).join('')
    : '<section class="panel"><h2>No notes yet</h2><p>The connected Supabase database is empty. The dashboard will populate after a signed client ingest creates the first note.</p></section>'
  return renderPage(
    'AuraBook Knowledge Dashboard',
    `<section class="grid">
      <div class="panel"><strong>Total notes</strong><br>${escapeHtml(String(stats.total_notes ?? 0))}</div>
      <div class="panel"><strong>Active agents</strong><br>${escapeHtml(String(stats.active_agents ?? 0))}</div>
      <div class="panel"><strong>Solved today</strong><br>${escapeHtml(String(stats.problems_solved_today ?? 0))}</div>
    </section>${noteHtml}`,
  )
}

function renderModels(payload: unknown): string {
  const result = payload as ModelListResponse
  const models = result.models.map((model) => `<li>${escapeHtml(model)}</li>`).join('')
  return renderPage(
    'AuraBook Model Check',
    `<section class="panel">
      <p><strong>Provider:</strong> ${escapeHtml(result.provider)}</p>
      <p><strong>Valid key:</strong> ${result.valid ? 'yes' : 'no'}</p>
      <p><strong>Default model:</strong> ${escapeHtml(result.default_model || 'none')}</p>
      ${result.error ? `<p class="muted">${escapeHtml(result.error)}</p>` : ''}
    </section>
    <section class="panel"><h2>Available models</h2><ul>${models}</ul></section>`,
  )
}

function renderCompare(payload: unknown): string {
  const result = payload as {
    provider_used?: string
    model_used?: string
    baseline_tokens?: number
    aurabook_tokens?: number
    injected_note?: string | null
    left_events?: DemoEvent[]
    right_events?: DemoEvent[]
  }
  const events = (label: string, items: DemoEvent[] = []) =>
    `<section class="panel"><p class="pill">Terminal Replay</p><h2>${escapeHtml(label)}</h2>${items.map((event) => `<h3 class="${event.kind === 'error' ? 'error' : event.kind === 'success' ? 'success' : ''}">${escapeHtml(event.status_label)}</h3><pre>${escapeHtml(event.text)}</pre><p class="muted">${escapeHtml(String(event.token_count))} tokens</p>`).join('')}</section>`
  return renderPage(
    'AuraBook Live Comparison',
    `<section class="panel">
      <p class="pill">Request complete</p>
      <p><strong>Provider:</strong> ${escapeHtml(String(result.provider_used ?? 'unknown'))}</p>
      <p><strong>Model:</strong> ${escapeHtml(String(result.model_used ?? 'unknown'))}</p>
      <p><strong>Baseline tokens:</strong> ${escapeHtml(String(result.baseline_tokens ?? 0))}</p>
      <p><strong>AuraBook tokens:</strong> ${escapeHtml(String(result.aurabook_tokens ?? 0))}</p>
      <p><strong>Injected note:</strong> ${escapeHtml(result.injected_note ?? 'No matching note was injected.')}</p>
    </section>
    <div class="grid">${events('Without AuraBook', result.left_events)}${events('With AuraBook', result.right_events)}</div>`,
  )
}

function escapeHtml(value: string): string {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;')
}

async function privateJson(
  request: Request,
  env: Env,
  cors: HeadersInit,
  handler: (bodyText: string) => Promise<unknown>,
): Promise<Response> {
  const bodyText = await request.text()
  const cached = loadIdempotentResponse(request)
  if (cached !== undefined) {
    return json(cached, cors)
  }

  const authError = await validateSignature(request, env, bodyText)
  if (authError) {
    return json({ detail: authError }, cors, 401)
  }

  try {
    const payload = await handler(bodyText)
    storeIdempotentResponse(request, payload)
    return json(payload, cors)
  } catch (error) {
    return json({ detail: errorMessage(error) }, cors, 400)
  }
}

function parseJson<T>(bodyText: string): T {
  try {
    return JSON.parse(bodyText || '{}') as T
  } catch {
    throw new Error('Invalid JSON body.')
  }
}

async function validateSignature(request: Request, env: Env, bodyText: string): Promise<string | null> {
  const secret = env.AURABOOK_CLOUD_API_SHARED_SECRET?.trim()
  if (!secret) {
    return null
  }

  const timestamp = request.headers.get('X-AuraBook-Timestamp') ?? ''
  const signature = request.headers.get('X-AuraBook-Signature') ?? ''
  if (!timestamp || !signature) {
    return 'Missing AuraBook signature headers'
  }

  const timestampValue = Number(timestamp)
  if (!Number.isFinite(timestampValue)) {
    return 'Invalid AuraBook timestamp'
  }

  const maxAge = Number(env.AURABOOK_CLOUD_API_SIGNATURE_MAX_AGE_S ?? '300')
  if (Math.abs(Date.now() / 1000 - timestampValue) > maxAge) {
    return 'Expired AuraBook signature timestamp'
  }

  const key = await crypto.subtle.importKey(
    'raw',
    new TextEncoder().encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  )
  const digest = await crypto.subtle.sign(
    'HMAC',
    key,
    new TextEncoder().encode(`${timestamp}.${bodyText}`),
  )
  const expected = [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, '0'))
    .join('')

  return timingSafeEqualHex(signature, expected) ? null : 'Invalid AuraBook signature'
}

function timingSafeEqualHex(left: string, right: string): boolean {
  if (left.length !== right.length) {
    return false
  }
  let diff = 0
  for (let index = 0; index < left.length; index += 1) {
    diff |= left.charCodeAt(index) ^ right.charCodeAt(index)
  }
  return diff === 0
}

function loadIdempotentResponse(request: Request): unknown | undefined {
  pruneIdempotencyCache()
  const key = request.headers.get('X-AuraBook-Idempotency-Key')?.trim()
  if (!key) {
    return undefined
  }
  const entry = idempotencyCache.get(key)
  if (!entry || Date.now() > entry.expiresAt) {
    idempotencyCache.delete(key)
    return undefined
  }
  return entry.payload
}

function storeIdempotentResponse(request: Request, payload: unknown): void {
  const key = request.headers.get('X-AuraBook-Idempotency-Key')?.trim()
  if (!key) {
    return
  }
  idempotencyCache.set(key, {
    expiresAt: Date.now() + IDEMPOTENCY_TTL_MS,
    payload,
  })
}

function pruneIdempotencyCache(): void {
  const now = Date.now()
  for (const [key, entry] of idempotencyCache) {
    if (now > entry.expiresAt) {
      idempotencyCache.delete(key)
    }
  }
}

async function dashboardSnapshot(env: Env): Promise<unknown> {
  const notes = await fetchSupabaseNotes(env)
  const dashboardNotes = notes.map((note) => toDashboardNote(note))
  const now = new Date().toISOString()

  return {
    notes: dashboardNotes,
    error_clusters: dashboardNotes.map((note) => ({
      id: note.error_cluster_id,
      trajectory_id: note.trajectory_id,
      note_id: note.id,
      problem_tag: note.problem_tag,
      summary: note.summary,
      diagnosis_summary: note.diagnosis_summary,
      full_diagnosis: note.full_diagnosis,
      root_error_excerpt: note.problem_description,
      root_error_detail: note.problem_description,
      latest_error_excerpt: null,
      unresolved: note.unsolved,
      created_at: note.created_at,
      updated_at: note.created_at,
      language: note.language,
      attempted_models: note.attempted_models,
      failed_model_count: note.failed_model_count,
      attempt_count: note.attempt_count,
      failure_count: note.unsolved_attempt_count,
      cascade_error_count: 0,
      related_trajectory_ids: note.trajectory_id ? [note.trajectory_id] : [],
      steps: [],
    })),
    stats: {
      total_notes: dashboardNotes.length,
      active_agents: 1,
      problems_solved_today: dashboardNotes.filter((note) => isToday(note.created_at)).length,
    },
    activities: dashboardNotes.slice(0, 8).map((note) => ({
      id: `activity-${note.id}`,
      text: note.unsolved ? `Unsolved cluster detected: ${note.problem_tag}` : `Knowledge note available: ${note.problem_tag}`,
      created_at: note.created_at ?? now,
      tone: note.unsolved ? 'warning' : 'success',
    })),
  }
}

async function ingestTrajectory(payload: IngestPayload, env: Env): Promise<unknown> {
  const trajectory = normalizeTrajectoryPayload(payload)
  const fingerprint = await trajectoryFingerprint(
    trajectory.original_prompt,
    trajectory.error_log,
    trajectory.final_fix,
  )

  const existing =
    (await fetchTrajectoryById(env, trajectory.trajectory_id)) ??
    (trajectory.cdc_hash ? await fetchTrajectoryByCdcHash(env, trajectory.cdc_hash) : null) ??
    (await fetchTrajectoryByFingerprint(env, fingerprint))

  if (existing) {
    const merged = mergeTrajectory(existing, trajectory, fingerprint)
    await supabasePatch(env, 'trajectories', { id: `eq.${existing.id}` }, merged.update)
    if (merged.requeue) {
      await createOrUpdateNote(env, { ...existing, ...merged.update, id: existing.id })
    }
    return {
      trajectory_id: existing.id,
      created: false,
      stored_nodes: 0,
      stored_edges: 0,
      queued_for_notes: merged.requeue,
    }
  }

  const row = {
    id: trajectory.trajectory_id,
    source: trajectory.source,
    client_id: trajectory.client_id,
    cdc_hash: trajectory.cdc_hash,
    original_goal: trajectory.original_goal,
    original_prompt: trajectory.original_prompt,
    broken_code: trajectory.broken_code,
    error_log: trajectory.error_log,
    ai_reflection: trajectory.ai_reflection,
    final_fix: trajectory.final_fix,
    trajectory_fingerprint: fingerprint,
    history: trajectory.history,
    metadata: trajectory.metadata,
    processed: false,
  }
  const inserted = await supabasePost<StoredTrajectoryRow[]>(env, 'trajectories', row)
  const stored = inserted[0] ?? { ...row, id: trajectory.trajectory_id }
  const nodeCount = await insertTrajectoryGraph(env, stored.id, trajectory)
  await createOrUpdateNote(env, stored)

  return {
    trajectory_id: stored.id,
    created: true,
    stored_nodes: nodeCount.nodes,
    stored_edges: nodeCount.edges,
    queued_for_notes: true,
  }
}

async function prefetchTrajectory(payload: { cdc_hash?: string }, env: Env): Promise<unknown> {
  const cdcHash = payload.cdc_hash?.trim()
  if (!cdcHash || cdcHash.length < 8) {
    throw new Error('cdc_hash must be at least 8 characters.')
  }

  const rows = await supabaseGet<Array<StoredTrajectoryRow & { trajectory_notes?: Array<{ id: string; note_text: string; source_model: string }> }>>(
    env,
    'trajectories',
    {
      select: 'id,cdc_hash,original_goal,ai_reflection,final_fix,trajectory_notes(id,note_text,source_model)',
      cdc_hash: `eq.${cdcHash}`,
      limit: '1',
    },
  )
  const trajectory = rows[0]
  if (!trajectory) {
    return { found: false, match: null }
  }
  const note = trajectory.trajectory_notes?.[0]
  return {
    found: true,
    match: {
      trajectory_id: trajectory.id,
      cdc_hash: trajectory.cdc_hash,
      note_id: note?.id ?? null,
      original_goal: trajectory.original_goal ?? null,
      final_fix: trajectory.final_fix,
      note_text: note?.note_text ?? null,
      source_model: note?.source_model ?? null,
      ai_reflection: trajectory.ai_reflection ?? null,
    },
  }
}

async function searchNotes(payload: SearchPayload, env: Env): Promise<unknown> {
  const queryText = [payload.prompt ?? '', payload.error_log ?? ''].filter((part) => part.trim()).join('\n\n')
  if (!queryText.trim()) {
    throw new Error('prompt or error_log is required.')
  }

  const limit = Math.max(1, Math.min(Number(payload.limit ?? 5), 25))
  const threshold = Number(
    payload.match_threshold ?? env.AURABOOK_CLOUD_SEARCH_MATCH_THRESHOLD ?? 0.75,
  )
  const embedding = await serverOpenAiEmbedding(queryText, env)
  const rows = await matchNotes(env, embedding, threshold, limit)
  await recordMatches(
    env,
    rows.map((row) => row.note_id).filter((noteId): noteId is string => typeof noteId === 'string' && Boolean(noteId)),
  )
  return {
    matches: rows.map((row) => ({
      note_id: row.note_id,
      trajectory_id: row.trajectory_id,
      note_text: row.note_text,
      source_model: row.source_model,
      similarity: Number(row.similarity ?? 0),
      metadata: { ...(row.metadata ?? {}), match_count: row.match_count ?? 0 },
      created_at: row.created_at,
      original_prompt: row.original_prompt ?? null,
      final_fix: row.final_fix ?? null,
      ai_reflection: row.ai_reflection ?? null,
      version: row.version ?? null,
      helpful_count: row.helpful_count ?? null,
      unhelpful_count: row.unhelpful_count ?? null,
      suppress_count: row.suppress_count ?? null,
    })),
  }
}

async function submitFeedback(payload: FeedbackPayload, env: Env): Promise<unknown> {
  if (!payload.note_id && !payload.trajectory_id) {
    throw new Error('note_id or trajectory_id is required.')
  }
  const feedback = payload.feedback
  if (feedback !== 'helpful' && feedback !== 'wrong' && feedback !== 'too_aggressive') {
    throw new Error('feedback must be helpful, wrong, or too_aggressive.')
  }

  let noteId = payload.note_id ?? null
  if (!noteId && payload.trajectory_id) {
    const notes = await supabaseGet<Array<{ id: string }>>(env, 'trajectory_notes', {
      select: 'id',
      trajectory_id: `eq.${payload.trajectory_id}`,
      limit: '1',
    })
    noteId = notes[0]?.id ?? null
  }

  if (noteId) {
    const counter = feedback === 'helpful' ? 'helpful_count' : feedback === 'wrong' ? 'unhelpful_count' : 'suppress_count'
    const rows = await supabaseGet<Array<Record<string, number>>>(env, 'trajectory_notes', {
      select: counter,
      id: `eq.${noteId}`,
      limit: '1',
    })
    const current = Number(rows[0]?.[counter] ?? 0)
    await supabasePatch(env, 'trajectory_notes', { id: `eq.${noteId}` }, { [counter]: current + 1 })
  }

  return {
    accepted: true,
    note_id: noteId,
    trajectory_id: payload.trajectory_id ?? null,
    feedback,
  }
}

async function pruneOldRows(env: Env): Promise<unknown> {
  const days = Math.max(1, Number(env.AURABOOK_CLOUD_RETENTION_DAYS ?? 90))
  const cutoff = new Date(Date.now() - days * 24 * 60 * 60 * 1000).toISOString()
  const rows = await supabaseGet<Array<{ id: string }>>(env, 'trajectories', {
    select: 'id',
    created_at: `lt.${cutoff}`,
  })
  if (rows.length > 0) {
    await supabaseDelete(env, 'trajectories', { created_at: `lt.${cutoff}` })
  }
  return { deleted: rows.length }
}

async function fetchSupabaseNotes(env: Env): Promise<NoteRow[]> {
  const base = required(env.AURABOOK_CLOUD_SUPABASE_URL, 'AURABOOK_CLOUD_SUPABASE_URL').replace(/\/$/, '')
  const key = required(env.AURABOOK_CLOUD_SUPABASE_SERVICE_ROLE_KEY, 'AURABOOK_CLOUD_SUPABASE_SERVICE_ROLE_KEY')
  const select = [
    'id',
    'trajectory_id',
    'note_text',
    'source_model',
    'metadata',
    'created_at',
    'updated_at',
    'helpful_count',
    'unhelpful_count',
    'suppress_count',
    'match_count',
    'trajectories(original_prompt,final_fix,error_log,ai_reflection,history,metadata,note_model,created_at,updated_at)',
  ].join(',')
  const response = await fetch(
    `${base}/rest/v1/trajectory_notes?select=${encodeURIComponent(select)}&order=created_at.desc&limit=24`,
    {
      headers: {
        apikey: key,
        authorization: `Bearer ${key}`,
      },
    },
  )
  if (!response.ok) {
    throw new Error(`Supabase snapshot failed: ${response.status} ${await response.text()}`)
  }
  return response.json<NoteRow[]>()
}

async function fetchTrajectoryById(env: Env, id: string): Promise<StoredTrajectoryRow | null> {
  const rows = await supabaseGet<StoredTrajectoryRow[]>(env, 'trajectories', {
    select: '*',
    id: `eq.${id}`,
    limit: '1',
  })
  return rows[0] ?? null
}

async function fetchTrajectoryByCdcHash(env: Env, cdcHash: string): Promise<StoredTrajectoryRow | null> {
  const rows = await supabaseGet<StoredTrajectoryRow[]>(env, 'trajectories', {
    select: '*',
    cdc_hash: `eq.${cdcHash}`,
    limit: '1',
  })
  return rows[0] ?? null
}

async function fetchTrajectoryByFingerprint(env: Env, fingerprint: string): Promise<StoredTrajectoryRow | null> {
  const rows = await supabaseGet<StoredTrajectoryRow[]>(env, 'trajectories', {
    select: '*',
    trajectory_fingerprint: `eq.${fingerprint}`,
    limit: '1',
  })
  return rows[0] ?? null
}

async function insertTrajectoryGraph(
  env: Env,
  trajectoryId: string,
  trajectory: NormalizedTrajectory,
): Promise<{ nodes: number; edges: number }> {
  const nodes = [
    { id: crypto.randomUUID(), trajectory_id: trajectoryId, node_type: 'prompt', content: trajectory.original_prompt, metadata: {} },
    { id: crypto.randomUUID(), trajectory_id: trajectoryId, node_type: 'attempt', content: trajectory.broken_code ?? '', metadata: {} },
    { id: crypto.randomUUID(), trajectory_id: trajectoryId, node_type: 'error', content: trajectory.error_log, metadata: {} },
  ]
  if (trajectory.ai_reflection) {
    nodes.push({
      id: crypto.randomUUID(),
      trajectory_id: trajectoryId,
      node_type: 'reflection',
      content: trajectory.ai_reflection,
      metadata: {},
    })
  }
  nodes.push({ id: crypto.randomUUID(), trajectory_id: trajectoryId, node_type: 'fix', content: trajectory.final_fix, metadata: {} })

  await supabasePost(env, 'trajectory_nodes', nodes)
  const nodeMap = Object.fromEntries(nodes.map((node) => [node.node_type, node.id]))
  const edges = [
    {
      id: crypto.randomUUID(),
      trajectory_id: trajectoryId,
      source_node_id: nodeMap.prompt,
      target_node_id: nodeMap.attempt,
      edge_type: 'prompt_to_attempt',
    },
    {
      id: crypto.randomUUID(),
      trajectory_id: trajectoryId,
      source_node_id: nodeMap.attempt,
      target_node_id: nodeMap.error,
      edge_type: 'attempt_to_error',
    },
  ]
  if (nodeMap.reflection) {
    edges.push(
      {
        id: crypto.randomUUID(),
        trajectory_id: trajectoryId,
        source_node_id: nodeMap.error,
        target_node_id: nodeMap.reflection,
        edge_type: 'error_to_reflection',
      },
      {
        id: crypto.randomUUID(),
        trajectory_id: trajectoryId,
        source_node_id: nodeMap.reflection,
        target_node_id: nodeMap.fix,
        edge_type: 'reflection_to_fix',
      },
    )
  } else {
    edges.push({
      id: crypto.randomUUID(),
      trajectory_id: trajectoryId,
      source_node_id: nodeMap.error,
      target_node_id: nodeMap.fix,
      edge_type: 'error_to_fix',
    })
  }
  await supabasePost(env, 'trajectory_edges', edges)
  return { nodes: nodes.length, edges: edges.length }
}

async function createOrUpdateNote(env: Env, trajectory: StoredTrajectoryRow): Promise<void> {
  const noteText = await summarizeTrajectory(trajectory, env)
  const embedding = await serverOpenAiEmbedding(searchableTrajectoryText(trajectory), env)
  const existing = await supabaseGet<Array<{ id: string; version: number; helpful_count: number; unhelpful_count: number; suppress_count: number; match_count: number }>>(
    env,
    'trajectory_notes',
    {
      select: 'id,version,helpful_count,unhelpful_count,suppress_count,match_count',
      trajectory_id: `eq.${trajectory.id}`,
      limit: '1',
    },
  )
  const current = existing[0]
  await supabasePost(env, 'trajectory_notes', {
    id: current?.id ?? crypto.randomUUID(),
    trajectory_id: trajectory.id,
    note_text: noteText,
    source_model: env.AURABOOK_CLOUD_OPENAI_API_KEY ? 'openai' : 'heuristic',
    embedding: `[${embedding.join(',')}]`,
    version: current ? Number(current.version ?? 1) + 1 : 1,
    helpful_count: current?.helpful_count ?? 0,
    unhelpful_count: current?.unhelpful_count ?? 0,
    suppress_count: current?.suppress_count ?? 0,
    match_count: current?.match_count ?? 0,
    last_matched_at: null,
    metadata: trajectory.metadata ?? {},
  }, 'trajectory_id')
  await supabasePatch(env, 'trajectories', { id: `eq.${trajectory.id}` }, {
    processed: true,
    note_model: env.AURABOOK_CLOUD_OPENAI_API_KEY ? 'openai' : 'heuristic',
    note_error: null,
    processed_at: new Date().toISOString(),
  })
}

async function matchNotes(
  env: Env,
  embedding: number[],
  threshold: number,
  limit: number,
): Promise<Array<Record<string, unknown>>> {
  const base = supabaseBase(env)
  const key = supabaseKey(env)
  const response = await fetch(`${base}/rest/v1/rpc/match_notes`, {
    method: 'POST',
    headers: {
      ...jsonHeaders,
      apikey: key,
      authorization: `Bearer ${key}`,
    },
    body: JSON.stringify({
      query_embedding: `[${embedding.join(',')}]`,
      match_threshold: threshold,
      result_limit: limit,
    }),
  })
  if (!response.ok) {
    throw new Error(`Supabase match_notes failed: ${response.status} ${await response.text()}`)
  }
  return response.json<Array<Record<string, unknown>>>()
}

async function recordMatches(env: Env, noteIds: string[]): Promise<void> {
  for (const noteId of noteIds) {
    const rows = await supabaseGet<Array<{ match_count: number }>>(env, 'trajectory_notes', {
      select: 'match_count',
      id: `eq.${noteId}`,
      limit: '1',
    })
    if (!rows[0]) {
      continue
    }
    await supabasePatch(env, 'trajectory_notes', { id: `eq.${noteId}` }, {
      match_count: Number(rows[0].match_count ?? 0) + 1,
      last_matched_at: new Date().toISOString(),
    })
  }
}

async function supabaseGet<T>(env: Env, table: string, query: Record<string, string>): Promise<T> {
  const url = new URL(`${supabaseBase(env)}/rest/v1/${table}`)
  for (const [key, value] of Object.entries(query)) {
    url.searchParams.set(key, value)
  }
  const response = await fetch(url, {
    headers: supabaseHeaders(env),
  })
  if (!response.ok) {
    throw new Error(`Supabase ${table} GET failed: ${response.status} ${await response.text()}`)
  }
  return response.json<T>()
}

async function supabasePost<T = unknown>(
  env: Env,
  table: string,
  payload: unknown,
  onConflict?: string,
): Promise<T> {
  const url = new URL(`${supabaseBase(env)}/rest/v1/${table}`)
  if (onConflict) {
    url.searchParams.set('on_conflict', onConflict)
  }
  const response = await fetch(url, {
    method: 'POST',
    headers: {
      ...supabaseHeaders(env),
      ...jsonHeaders,
      Prefer: onConflict ? 'resolution=merge-duplicates,return=representation' : 'return=representation',
    },
    body: JSON.stringify(payload),
  })
  if (!response.ok) {
    throw new Error(`Supabase ${table} POST failed: ${response.status} ${await response.text()}`)
  }
  return response.json<T>()
}

async function supabasePatch<T = unknown>(
  env: Env,
  table: string,
  query: Record<string, string>,
  payload: unknown,
): Promise<T | null> {
  const url = new URL(`${supabaseBase(env)}/rest/v1/${table}`)
  for (const [key, value] of Object.entries(query)) {
    url.searchParams.set(key, value)
  }
  const response = await fetch(url, {
    method: 'PATCH',
    headers: {
      ...supabaseHeaders(env),
      ...jsonHeaders,
      Prefer: 'return=representation',
    },
    body: JSON.stringify(payload),
  })
  if (!response.ok) {
    throw new Error(`Supabase ${table} PATCH failed: ${response.status} ${await response.text()}`)
  }
  return response.status === 204 ? null : response.json<T>()
}

async function supabaseDelete(env: Env, table: string, query: Record<string, string>): Promise<void> {
  const url = new URL(`${supabaseBase(env)}/rest/v1/${table}`)
  for (const [key, value] of Object.entries(query)) {
    url.searchParams.set(key, value)
  }
  const response = await fetch(url, {
    method: 'DELETE',
    headers: supabaseHeaders(env),
  })
  if (!response.ok) {
    throw new Error(`Supabase ${table} DELETE failed: ${response.status} ${await response.text()}`)
  }
}

function supabaseHeaders(env: Env): HeadersInit {
  const key = supabaseKey(env)
  return {
    apikey: key,
    authorization: `Bearer ${key}`,
  }
}

function supabaseBase(env: Env): string {
  return required(env.AURABOOK_CLOUD_SUPABASE_URL, 'AURABOOK_CLOUD_SUPABASE_URL').replace(/\/$/, '')
}

function supabaseKey(env: Env): string {
  return required(env.AURABOOK_CLOUD_SUPABASE_SERVICE_ROLE_KEY, 'AURABOOK_CLOUD_SUPABASE_SERVICE_ROLE_KEY')
}

function toDashboardNote(row: NoteRow): DashboardNote {
  const trajectory = row.trajectories ?? {}
  const metadata = { ...(trajectory.metadata ?? {}), ...(row.metadata ?? {}) }
  const score = (row.helpful_count ?? 0) - (row.unhelpful_count ?? 0) - (row.suppress_count ?? 0)
  const problemTag = stringMeta(metadata, 'problem_tag') || inferProblemTag(row.note_text, trajectory.error_log)
  const language = normalizeLanguage(stringMeta(metadata, 'language') || inferLanguage(row.note_text, trajectory.original_prompt))
  const attemptedModels = arrayMeta(metadata, 'attempted_models')
  const createdAt = row.created_at ?? trajectory.created_at ?? new Date().toISOString()

  return {
    id: row.id,
    error_cluster_id: `cluster-${row.id}`,
    trajectory_id: row.trajectory_id,
    problem_tag: problemTag,
    summary: firstSentence(row.note_text),
    diagnosis_summary: stringMeta(metadata, 'diagnosis_summary') || firstSentence(trajectory.ai_reflection ?? row.note_text),
    full_diagnosis: trajectory.ai_reflection || row.note_text,
    model: row.source_model || trajectory.note_model || 'AuraBook',
    created_at: createdAt,
    velocity: 'stable',
    status: 'Stable',
    score,
    unsolved: Boolean(metadata.resolved === false),
    language,
    problem_description: trajectory.error_log || trajectory.original_prompt || row.note_text,
    solution_text: trajectory.final_fix || row.note_text,
    environment: {
      os: stringMeta(metadata, 'os') || 'Unknown',
      language_version: stringMeta(metadata, 'language_version') || 'Unknown runtime',
      libraries: arrayMeta(metadata, 'libraries'),
    },
    usage_count: row.match_count ?? 0,
    attempted_models: attemptedModels,
    unsolved_attempt_count: Number(metadata.unsolved_attempt_count ?? 0),
    failed_model_count: Number(metadata.failed_model_count ?? attemptedModels.length),
    attempt_count: Number(metadata.attempt_count ?? attemptedModels.length),
    attempts: [],
    rating_history: [{ timestamp: createdAt, score }],
    comments: [],
    community_context: row.note_text,
  }
}

function normalizeTrajectoryPayload(payload: IngestPayload): NormalizedTrajectory {
  const originalPrompt = payload.original_prompt ?? payload.trigger_prompt
  const errorLog = payload.error_log ?? payload.previous_error_log
  const finalFix = payload.final_fix ?? payload.final_code ?? payload.fix

  if (!originalPrompt?.trim()) {
    throw new Error('original_prompt is required.')
  }
  if (!errorLog?.trim()) {
    throw new Error('error_log is required.')
  }
  if (!finalFix?.trim()) {
    throw new Error('final_fix is required.')
  }

  return {
    trajectory_id: (payload.trajectory_id ?? payload.id ?? crypto.randomUUID()).trim(),
    source: payload.source ?? 'local_client',
    client_id: payload.client_id ?? null,
    cdc_hash: payload.cdc_hash ?? null,
    original_goal: payload.original_goal ?? null,
    original_prompt: originalPrompt,
    broken_code: payload.broken_code ?? payload.attempt_code ?? payload.attempt ?? null,
    error_log: errorLog,
    ai_reflection: payload.ai_reflection ?? null,
    final_fix: finalFix,
    history: Array.isArray(payload.history) ? payload.history : [],
    metadata: payload.metadata && typeof payload.metadata === 'object' ? payload.metadata : {},
  }
}

function mergeTrajectory(
  existing: StoredTrajectoryRow,
  incoming: NormalizedTrajectory,
  fingerprint: string,
): { update: Partial<StoredTrajectoryRow>; requeue: boolean } {
  const history = mergeHistory(existing.history ?? [], incoming.history)
  const metadata = {
    ...(existing.metadata ?? {}),
    ...incoming.metadata,
    attempt_count: history.length,
    attempted_models: uniqueStrings([
      ...arrayMeta(existing.metadata ?? {}, 'attempted_models'),
      ...arrayMeta(incoming.metadata, 'attempted_models'),
      ...history.map((turn) => (typeof turn.model === 'string' ? turn.model : '')).filter(Boolean),
    ]),
    last_ingested_at: new Date().toISOString(),
  }

  const finalFix = isResolvedFinalFix(incoming.final_fix) || !isResolvedFinalFix(existing.final_fix)
    ? incoming.final_fix
    : existing.final_fix
  const update: Partial<StoredTrajectoryRow> = {
    source: existing.source,
    client_id: incoming.client_id ?? existing.client_id ?? null,
    cdc_hash: incoming.cdc_hash ?? existing.cdc_hash ?? null,
    original_goal: incoming.original_goal ?? existing.original_goal ?? null,
    original_prompt: longest(existing.original_prompt, incoming.original_prompt),
    broken_code: incoming.broken_code ?? existing.broken_code ?? null,
    error_log: longest(existing.error_log, incoming.error_log),
    ai_reflection: incoming.ai_reflection ?? existing.ai_reflection ?? null,
    final_fix: finalFix,
    trajectory_fingerprint: fingerprint,
    history,
    metadata,
  }
  const requeue = Boolean(
    !existing.processed ||
      update.final_fix !== existing.final_fix ||
      update.ai_reflection !== existing.ai_reflection ||
      update.broken_code !== existing.broken_code ||
      update.error_log !== existing.error_log ||
      update.original_prompt !== existing.original_prompt,
  )
  if (requeue) {
    update.processed = false
    update.note_error = null
  }
  return { update, requeue }
}

async function summarizeTrajectory(trajectory: StoredTrajectoryRow, env: Env): Promise<string> {
  const key = env.AURABOOK_CLOUD_OPENAI_API_KEY?.trim()
  if (!key) {
    return heuristicNote(trajectory)
  }

  const prompt = [
    `Original goal:\n${trajectory.original_goal ?? ''}`,
    `Original prompt:\n${trajectory.original_prompt}`,
    `Broken code or attempt:\n${trajectory.broken_code ?? ''}`,
    `Error log:\n${trajectory.error_log}`,
    `Attempt history:\n${formatHistory(trajectory.history ?? [])}`,
    `Attempted models:\n${JSON.stringify((trajectory.metadata ?? {}).attempted_models ?? [])}`,
    `Final fix:\n${trajectory.final_fix}`,
  ].join('\n\n')

  try {
    const result = await generateOpenAiCompatible(
      'openai',
      key,
      'gpt-4o-mini',
      'You convert one coding failure trajectory into a concise system note. Output one to two sentences only. Start with "System Note:". State the root cause and the concrete corrective action. Do not include markdown, bullets, code fences, or line breaks.',
      prompt,
    )
    return normalizeSystemNote(result.text)
  } catch {
    return heuristicNote(trajectory)
  }
}

async function serverOpenAiEmbedding(text: string, env: Env): Promise<number[]> {
  const key = required(env.AURABOOK_CLOUD_OPENAI_API_KEY, 'AURABOOK_CLOUD_OPENAI_API_KEY')
  const embedding = await openAiEmbedding(text, key)
  if (!embedding) {
    throw new Error('OpenAI embedding request returned no embedding.')
  }
  return embedding
}

function searchableTrajectoryText(trajectory: StoredTrajectoryRow): string {
  return [
    trajectory.original_goal ?? '',
    trajectory.original_prompt,
    trajectory.broken_code ?? '',
    trajectory.error_log,
    trajectory.ai_reflection ?? '',
    trajectory.final_fix,
  ]
    .filter((part) => part.trim())
    .join('\n\n')
}

function heuristicNote(trajectory: StoredTrajectoryRow): string {
  if (trajectory.ai_reflection?.trim()) {
    return normalizeSystemNote(trajectory.ai_reflection)
  }
  const fix = trajectory.final_fix.trim().replace(/\s+/g, ' ')
  if (fix) {
    return `System Note: Prefer the verified repair pattern: ${fix.slice(0, 180).replace(/[.!?]*$/, '')}.`
  }
  const error = trajectory.error_log.trim().replace(/\s+/g, ' ')
  return `System Note: Avoid the previous failure mode: ${error.slice(0, 180).replace(/[.!?]*$/, '')}.`
}

function normalizeSystemNote(text: string): string {
  const cleaned = text.trim().replace(/\s+/g, ' ')
  const withPrefix = cleaned.toLowerCase().startsWith('system note:')
    ? cleaned
    : `System Note: ${cleaned}`
  const sentences = withPrefix.split(/(?<=[.!?])\s+/).slice(0, 2).join(' ').trim()
  return /[.!?]$/.test(sentences) ? sentences : `${sentences}.`
}

async function trajectoryFingerprint(originalPrompt: string, errorLog: string, finalFix: string): Promise<string> {
  const payload = [normalizeText(originalPrompt), normalizeText(errorLog), normalizeText(finalFix)].join('||')
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(payload))
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, '0')).join('')
}

function normalizeText(text: string): string {
  return text
    .toLowerCase()
    .replace(/[A-Za-z]:\\[^\s]+|\/[\w./-]+/g, ' ')
    .replace(/line \d+|:\d+\b/gi, ' ')
    .replace(/settingwithcopywarning/gi, 'pandas slice assignment warning')
    .replace(/corsmiddleware/gi, 'cors middleware')
    .replace(/httpx\.asyncclient/gi, 'httpx async client')
    .replace(/useeffect/gi, 'use effect')
    .replace(/traceback/gi, 'traceback')
    .replace(/\s+/g, ' ')
    .trim()
}

function mergeHistory(existing: Array<Record<string, unknown>>, incoming: Array<Record<string, unknown>>): Array<Record<string, unknown>> {
  const seen = new Set<string>()
  const merged: Array<Record<string, unknown>> = []
  for (const turn of [...existing, ...incoming]) {
    const key = JSON.stringify([
      turn.model,
      turn.prompt,
      turn.response,
      turn.thought,
      turn.tried,
      turn.outcome,
      turn.commands,
      turn.changed_files,
      turn.token_count,
      turn.duration_ms,
    ])
    if (seen.has(key)) {
      continue
    }
    seen.add(key)
    merged.push(turn)
  }
  return merged.sort((left, right) => String(left.timestamp ?? '').localeCompare(String(right.timestamp ?? '')))
}

function isResolvedFinalFix(finalFix: string): boolean {
  return !finalFix.trim().toUpperCase().startsWith('UNRESOLVED')
}

function longest(left: string, right: string): string {
  return right.trim().length > left.trim().length ? right : left
}

function uniqueStrings(values: string[]): string[] {
  const seen = new Set<string>()
  const result: string[] = []
  for (const value of values) {
    const cleaned = value.trim()
    if (!cleaned || seen.has(cleaned)) {
      continue
    }
    seen.add(cleaned)
    result.push(cleaned)
  }
  return result
}

function formatHistory(history: Array<Record<string, unknown>>): string {
  if (!history.length) {
    return 'No recorded attempts.'
  }
  return history
    .map((turn, index) =>
      [
        `Attempt ${index + 1}`,
        typeof turn.model === 'string' ? `Model: ${turn.model}` : '',
        typeof turn.thought === 'string' ? `Thought: ${turn.thought}` : '',
        typeof turn.tried === 'string' ? `Tried: ${turn.tried}` : '',
        typeof turn.outcome === 'string' ? `Outcome: ${turn.outcome}` : '',
        Array.isArray(turn.commands) ? `Commands: ${turn.commands.join(', ')}` : '',
        Array.isArray(turn.changed_files) ? `Changed files: ${turn.changed_files.join(', ')}` : '',
      ]
        .filter(Boolean)
        .join('\n'),
    )
    .join('\n\n')
}

async function liveCompare(prompt: string, apiKey: string, model: string | undefined, env: Env): Promise<unknown> {
  if (!prompt.trim()) {
    throw new Error('Prompt is required.')
  }
  if (!apiKey.trim()) {
    throw new Error('API key is required.')
  }

  const provider = detectProvider(apiKey, model)
  const modelName = model?.trim() || defaultModel(provider)
  let injectedNote: string | null = null
  let retrievalProviderCallCount = 0

  try {
    const embedding = provider === 'openai' ? await openAiEmbedding(prompt, apiKey) : null
    if (embedding) {
      retrievalProviderCallCount = 1
      injectedNote = await findMatchingNote(env, embedding)
    }
  } catch {
    injectedNote = null
  }

  const leftEvents: DemoEvent[] = [
    {
      at: 200,
      kind: 'agent',
      text: `Calling ${modelName} without AuraBook context...`,
      token_count: 0,
      status_label: `Calling ${provider}`,
    },
  ]
  const rightEvents: DemoEvent[] = [
    {
      at: 200,
      kind: 'agent',
      text: 'Querying AuraBook knowledge base for community context...',
      token_count: 0,
      status_label: 'Searching knowledge base',
    },
    injectedNote
      ? {
          at: 700,
          kind: 'note',
          text: `Community note found:\n\n${injectedNote}`,
          token_count: 0,
          status_label: 'Note injected',
        }
      : {
          at: 700,
          kind: 'agent',
          text: 'No matching community note - proceeding with standard context.',
          token_count: 0,
          status_label: 'No note found',
        },
    {
      at: 1000,
      kind: 'agent',
      text: `Calling ${modelName} with injected community context...`,
      token_count: 0,
      status_label: `Calling ${provider}`,
    },
  ]

  let baselineTokens = 0
  let aurabookTokens = 0
  const baseline = await generateText(provider, apiKey, modelName, systemNoNote(), prompt)
  baselineTokens = baseline.totalTokens
  leftEvents.push({
    at: 2500,
    kind: 'success',
    text: baseline.text,
    token_count: baselineTokens,
    status_label: 'Complete',
  })

  const aura = await generateText(
    provider,
    apiKey,
    modelName,
    injectedNote ? systemWithNote(injectedNote) : systemNoNote(),
    prompt,
  )
  aurabookTokens = aura.totalTokens
  rightEvents.push({
    at: 2800,
    kind: 'success',
    text: aura.text,
    token_count: aurabookTokens,
    status_label: 'Complete',
  })

  return {
    injected_note: injectedNote,
    left_events: leftEvents,
    right_events: rightEvents,
    baseline_tokens: baselineTokens,
    aurabook_tokens: aurabookTokens,
    total_tokens: baselineTokens + aurabookTokens,
    provider_used: provider,
    model_used: modelName,
    retrieval_provider_call_count: retrievalProviderCallCount,
    provider_call_count: 2 + retrievalProviderCallCount,
  }
}

async function findMatchingNote(env: Env, embedding: number[]): Promise<string | null> {
  const base = required(env.AURABOOK_CLOUD_SUPABASE_URL, 'AURABOOK_CLOUD_SUPABASE_URL').replace(/\/$/, '')
  const key = required(env.AURABOOK_CLOUD_SUPABASE_SERVICE_ROLE_KEY, 'AURABOOK_CLOUD_SUPABASE_SERVICE_ROLE_KEY')
  const response = await fetch(`${base}/rest/v1/rpc/match_notes`, {
    method: 'POST',
    headers: {
      ...jsonHeaders,
      apikey: key,
      authorization: `Bearer ${key}`,
    },
    body: JSON.stringify({
      query_embedding: `[${embedding.join(',')}]`,
      match_threshold: 0.25,
      result_limit: 1,
    }),
  })
  if (!response.ok) {
    return null
  }
  const rows = await response.json<Array<{ note_text?: string }>>()
  return rows[0]?.note_text ?? null
}

async function openAiEmbedding(prompt: string, apiKey: string): Promise<number[] | null> {
  const response = await fetch('https://api.openai.com/v1/embeddings', {
    method: 'POST',
    headers: {
      ...jsonHeaders,
      authorization: `Bearer ${apiKey}`,
    },
    body: JSON.stringify({
      model: 'text-embedding-3-small',
      input: prompt,
    }),
  })
  if (!response.ok) {
    return null
  }
  const payload = await response.json<{ data?: Array<{ embedding?: number[] }> }>()
  return payload.data?.[0]?.embedding ?? null
}

async function generateText(
  provider: string,
  apiKey: string,
  model: string,
  systemPrompt: string,
  userPrompt: string,
): Promise<ProviderResult> {
  if (provider === 'claude') {
    return generateAnthropic(apiKey, model, systemPrompt, userPrompt)
  }
  if (provider === 'gemini') {
    return generateGemini(apiKey, model, systemPrompt, userPrompt)
  }
  return generateOpenAiCompatible(provider, apiKey, model, systemPrompt, userPrompt)
}

async function generateOpenAiCompatible(
  provider: string,
  apiKey: string,
  model: string,
  systemPrompt: string,
  userPrompt: string,
): Promise<ProviderResult> {
  const base = provider === 'deepseek' ? 'https://api.deepseek.com/v1' : 'https://api.openai.com/v1'
  const response = await fetch(`${base}/chat/completions`, {
    method: 'POST',
    headers: {
      ...jsonHeaders,
      authorization: `Bearer ${apiKey}`,
    },
    body: JSON.stringify({
      model,
      messages: [
        { role: 'system', content: systemPrompt },
        { role: 'user', content: userPrompt },
      ],
      max_tokens: 512,
      temperature: 0.2,
    }),
  })
  const payload = await response.json<Record<string, unknown>>()
  if (!response.ok) {
    throw new Error(providerError(payload, response.status))
  }
  const choice = (payload.choices as Array<{ message?: { content?: string } }> | undefined)?.[0]
  const usage = payload.usage as { total_tokens?: number } | undefined
  const text = choice?.message?.content ?? ''
  return { text, totalTokens: usage?.total_tokens ?? estimateTokens(systemPrompt + userPrompt + text) }
}

async function generateAnthropic(
  apiKey: string,
  model: string,
  systemPrompt: string,
  userPrompt: string,
): Promise<ProviderResult> {
  const response = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: {
      ...jsonHeaders,
      'x-api-key': apiKey,
      'anthropic-version': '2023-06-01',
    },
    body: JSON.stringify({
      model,
      system: systemPrompt,
      messages: [{ role: 'user', content: userPrompt }],
      max_tokens: 512,
    }),
  })
  const payload = await response.json<Record<string, unknown>>()
  if (!response.ok) {
    throw new Error(providerError(payload, response.status))
  }
  const content = payload.content as Array<{ text?: string }> | undefined
  const usage = payload.usage as { input_tokens?: number; output_tokens?: number } | undefined
  const text = content?.map((item) => item.text ?? '').join('') ?? ''
  return {
    text,
    totalTokens: (usage?.input_tokens ?? 0) + (usage?.output_tokens ?? 0) || estimateTokens(systemPrompt + userPrompt + text),
  }
}

async function generateGemini(
  apiKey: string,
  model: string,
  systemPrompt: string,
  userPrompt: string,
): Promise<ProviderResult> {
  const response = await fetch(
    `https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(model)}:generateContent?key=${encodeURIComponent(apiKey)}`,
    {
      method: 'POST',
      headers: jsonHeaders,
      body: JSON.stringify({
        systemInstruction: { parts: [{ text: systemPrompt }] },
        contents: [{ role: 'user', parts: [{ text: userPrompt }] }],
        generationConfig: { maxOutputTokens: 512, temperature: 0.2 },
      }),
    },
  )
  const payload = await response.json<Record<string, unknown>>()
  if (!response.ok) {
    throw new Error(providerError(payload, response.status))
  }
  const candidates = payload.candidates as Array<{ content?: { parts?: Array<{ text?: string }> } }> | undefined
  const usage = payload.usageMetadata as { totalTokenCount?: number } | undefined
  const text = candidates?.[0]?.content?.parts?.map((part) => part.text ?? '').join('') ?? ''
  return { text, totalTokens: usage?.totalTokenCount ?? estimateTokens(systemPrompt + userPrompt + text) }
}

async function listModelsForKey(apiKey: string, providerInput?: string): Promise<unknown> {
  const provider = normalizeProvider(providerInput || detectProvider(apiKey))
  if (!apiKey.trim()) {
    return { ...warmModels(provider), valid: false, default_model: '', error: 'API key is required.' }
  }

  try {
    const models = await fetchModels(provider, apiKey)
    return {
      provider,
      valid: true,
      models,
      default_model: models[0] ?? '',
      cache_status: 'live',
      error: null,
    }
  } catch (error) {
    return {
      ...warmModels(provider),
      valid: false,
      default_model: '',
      cache_status: 'warm',
      error: errorMessage(error),
    }
  }
}

async function fetchModels(provider: string, apiKey: string): Promise<string[]> {
  if (provider === 'claude') {
    const response = await fetch('https://api.anthropic.com/v1/models', {
      headers: { 'x-api-key': apiKey, 'anthropic-version': '2023-06-01' },
    })
    const payload = await response.json<Record<string, unknown>>()
    if (!response.ok) throw new Error(providerError(payload, response.status))
    return sortedModels(
      ((payload.data as Array<{ id?: string }> | undefined) ?? [])
        .map((item) => item.id)
        .filter((id): id is string => Boolean(id?.startsWith('claude-'))),
    )
  }
  if (provider === 'gemini') {
    const response = await fetch(`https://generativelanguage.googleapis.com/v1beta/models?key=${encodeURIComponent(apiKey)}`)
    const payload = await response.json<Record<string, unknown>>()
    if (!response.ok) throw new Error(providerError(payload, response.status))
    return sortedModels(
      ((payload.models as Array<{ name?: string; supportedGenerationMethods?: string[] }> | undefined) ?? [])
        .filter((item) => item.supportedGenerationMethods?.includes('generateContent'))
        .map((item) => item.name?.replace(/^models\//, ''))
        .filter((id): id is string => Boolean(id)),
    )
  }

  const base = provider === 'deepseek' ? 'https://api.deepseek.com/v1' : 'https://api.openai.com/v1'
  const response = await fetch(`${base}/models`, {
    headers: { authorization: `Bearer ${apiKey}` },
  })
  const payload = await response.json<Record<string, unknown>>()
  if (!response.ok) throw new Error(providerError(payload, response.status))
  const ids = ((payload.data as Array<{ id?: string }> | undefined) ?? [])
    .map((item) => item.id)
    .filter((id): id is string => Boolean(id))
  if (provider === 'deepseek') {
    return sortedModels(ids.filter((id) => id.startsWith('deepseek-')))
  }
  return sortedModels(
    ids.filter(
      (id) =>
        /^(gpt-|o1|o3|o4|chatgpt-)/.test(id) &&
        !/(audio|embedding|moderation|realtime|search|speech|transcribe|tts|whisper|image|dall-e)/.test(id),
    ),
  )
}

function warmModels(providerInput: string): ModelListResponse {
  const provider = normalizeProvider(providerInput)
  const models =
    provider === 'claude'
      ? ['claude-sonnet-4-20250514', 'claude-haiku-4-5-20251001']
      : provider === 'gemini'
        ? ['gemini-2.5-flash', 'gemini-2.5-pro', 'gemini-1.5-flash']
        : provider === 'deepseek'
          ? ['deepseek-chat', 'deepseek-reasoner']
          : ['gpt-4o-mini', 'gpt-4o']
  return {
    provider,
    valid: false,
    models,
    default_model: models[0],
    cache_status: 'fallback',
    error: 'Using fallback list until the API key is validated.',
  }
}

function detectProvider(apiKey: string, model?: string): string {
  const key = apiKey.trim()
  const selectedModel = model?.trim() ?? ''
  if (selectedModel.startsWith('deepseek-')) return 'deepseek'
  if (key.startsWith('sk-ant-')) return 'claude'
  if (key.startsWith('AIza')) return 'gemini'
  return 'openai'
}

function normalizeProvider(provider: string): string {
  const cleaned = provider.trim().toLowerCase()
  if (cleaned === 'anthropic') return 'claude'
  return cleaned || 'openai'
}

function defaultModel(provider: string): string {
  if (provider === 'claude') return 'claude-haiku-4-5-20251001'
  if (provider === 'gemini') return 'gemini-1.5-flash'
  if (provider === 'deepseek') return 'deepseek-chat'
  return 'gpt-4o-mini'
}

function sortedModels(models: string[]): string[] {
  const unique = [...new Set(models.filter(Boolean))].sort()
  const preferred = unique.filter((model) =>
    ['gpt-5', 'gpt-4.1', 'gpt-4o', 'o4', 'claude', 'gemini-2.5', 'deepseek'].some((marker) =>
      model.includes(marker),
    ),
  )
  return [...preferred, ...unique.filter((model) => !preferred.includes(model))]
}

function systemNoNote(): string {
  return 'You are a helpful AI coding assistant. Answer the user coding request concisely and precisely in 3-5 sentences.'
}

function systemWithNote(note: string): string {
  return `You are a helpful AI coding assistant powered by AuraBook, a shared agent memory network.

The community has surfaced a relevant knowledge note for this request:
---
${note}
---

Use this context to solve the user's request efficiently. Skip the trial-and-error paths the community has already documented. Keep your answer concise (3-5 sentences).`
}

function firstSentence(value: string | undefined): string {
  const cleaned = (value ?? '').trim().replace(/\s+/g, ' ')
  if (!cleaned) return 'AuraBook knowledge note'
  return cleaned.split(/(?<=[.!?])\s+/)[0]?.slice(0, 180) ?? cleaned.slice(0, 180)
}

function inferProblemTag(noteText: string, errorLog?: string): string {
  const text = `${noteText} ${errorLog ?? ''}`.toLowerCase()
  if (text.includes('pygame')) return 'pygame-runtime'
  if (text.includes('fastapi')) return 'fastapi'
  if (text.includes('prisma')) return 'prisma'
  if (text.includes('supabase') || text.includes('postgres')) return 'database'
  return 'coding-pattern'
}

function inferLanguage(noteText: string, prompt?: string): string {
  const text = `${noteText} ${prompt ?? ''}`.toLowerCase()
  if (text.includes('typescript')) return 'TypeScript'
  if (text.includes('javascript')) return 'JavaScript'
  if (text.includes('rust')) return 'Rust'
  return 'Python'
}

function normalizeLanguage(value: string): string {
  return ['Python', 'JavaScript', 'TypeScript', 'Rust'].includes(value) ? value : 'Python'
}

function stringMeta(metadata: Record<string, unknown>, key: string): string {
  const value = metadata[key]
  return typeof value === 'string' ? value : ''
}

function arrayMeta(metadata: Record<string, unknown>, key: string): string[] {
  const value = metadata[key]
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}

function isToday(value: string): boolean {
  return new Date(value).toDateString() === new Date().toDateString()
}

function estimateTokens(value: string): number {
  return Math.max(1, Math.ceil(value.length / 4))
}

function required(value: string | undefined, name: string): string {
  if (!value?.trim()) {
    throw new Error(`${name} is not configured.`)
  }
  return value.trim()
}

function providerError(payload: Record<string, unknown>, status: number): string {
  const error = payload.error
  if (typeof error === 'object' && error && 'message' in error) {
    return String((error as { message: unknown }).message)
  }
  if (typeof error === 'string') return error
  if (typeof payload.message === 'string') return payload.message
  return `Provider request failed with HTTP ${status}`
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}
