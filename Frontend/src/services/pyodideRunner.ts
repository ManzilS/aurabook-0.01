export interface PythonRunResult {
  code: string
  stdout: string
  stderr: string
  error: string | null
}

export function extractPythonSnippets(text: string): string[] {
  const snippets = [...text.matchAll(/```(?:python|py)\s*\n([\s\S]*?)```/gi)]
    .map((match) => match[1]?.trim())
    .filter((snippet): snippet is string => Boolean(snippet))
  if (snippets.length) return snippets
  const trimmed = text.trim()
  if (trimmed.length <= 4000 && /(^|\n)\s*(print\(|import\s+\w+|from\s+\w+\s+import|def\s+\w+\(|class\s+\w+|for\s+\w+\s+in\s+|if\s+.+:)/.test(trimmed)) {
    return [trimmed.replace(/^Here(?:'s| is).+?:\s*/i, '').replace(/^`+|`+$/g, '').trim()]
  }
  return []
}

// A disposable worker isolates run state and lets us terminate infinite loops.
export function runPythonSnippet(code: string, timeoutMs = 30000): Promise<PythonRunResult> {
  return new Promise((resolve) => {
    const worker = new Worker(new URL('../../python-worker.js', import.meta.url))
    let timer: ReturnType<typeof setTimeout>
    const finish = (result: Omit<PythonRunResult, 'code'>) => {
      clearTimeout(timer)
      worker.terminate()
      resolve({ code, ...result })
    }
    timer = setTimeout(() => finish({ stdout: '', stderr: '', error: 'Python runtime could not load within 90 seconds.' }), 90000)
    worker.onmessage = (event) => {
      if (event.data.type === 'ready') {
        clearTimeout(timer)
        timer = setTimeout(() => finish({ stdout: '', stderr: '', error: 'Python execution exceeded its time limit.' }), timeoutMs)
      } else if (event.data.type === 'result') {
        finish(event.data.result)
      }
    }
    worker.onerror = (event) => finish({ stdout: '', stderr: '', error: event.message || 'Python worker failed.' })
    worker.postMessage({ code })
  })
}
