interface PyodideRuntime {
  globals: {
    set: (name: string, value: unknown) => void
    delete: (name: string) => void
  }
  runPython: (code: string) => unknown
  runPythonAsync: (code: string) => Promise<unknown>
}

declare global {
  interface Window {
    loadPyodide?: (options: { indexURL: string }) => Promise<PyodideRuntime>
    aurabookPyodide?: Promise<PyodideRuntime>
  }
}

export interface PythonRunResult {
  code: string
  stdout: string
  stderr: string
  error: string | null
}

const PYODIDE_VERSION = '0.27.7'
const PYODIDE_INDEX_URL = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`
const PYODIDE_SCRIPT_URL = `${PYODIDE_INDEX_URL}pyodide.js`

export function extractPythonSnippets(text: string): string[] {
  const fencedSnippets = [...text.matchAll(/```(?:python|py)\s*\n([\s\S]*?)```/gi)]
    .map((match) => match[1]?.trim())
    .filter((snippet): snippet is string => Boolean(snippet))

  if (fencedSnippets.length > 0) {
    return fencedSnippets
  }

  const trimmed = text.trim()
  if (looksLikePython(trimmed)) {
    return [stripPlainCode(trimmed)]
  }

  return []
}

export async function runPythonSnippet(code: string): Promise<PythonRunResult> {
  const pyodide = await loadPyodideRuntime()
  pyodide.globals.set('_aurabook_code', code)
  try {
    await pyodide.runPythonAsync(`
import io
import json
import sys
import traceback

_stdout = io.StringIO()
_stderr = io.StringIO()
_old_stdout = sys.stdout
_old_stderr = sys.stderr
_error = None

try:
    sys.stdout = _stdout
    sys.stderr = _stderr
    exec(_aurabook_code, {})
except Exception:
    _error = traceback.format_exc()
finally:
    sys.stdout = _old_stdout
    sys.stderr = _old_stderr

_aurabook_result_json = json.dumps({
    "stdout": _stdout.getvalue(),
    "stderr": _stderr.getvalue(),
    "error": _error,
})
`)
    const rawResult = pyodide.runPython('_aurabook_result_json')
    const parsed = JSON.parse(String(rawResult)) as Omit<PythonRunResult, 'code'>
    return {
      code,
      stdout: parsed.stdout,
      stderr: parsed.stderr,
      error: parsed.error,
    }
  } finally {
    pyodide.globals.delete('_aurabook_code')
  }
}

async function loadPyodideRuntime(): Promise<PyodideRuntime> {
  if (window.aurabookPyodide) {
    return window.aurabookPyodide
  }

  window.aurabookPyodide = loadPyodideScript().then(() => {
    if (!window.loadPyodide) {
      throw new Error('Pyodide failed to initialize.')
    }
    return window.loadPyodide({ indexURL: PYODIDE_INDEX_URL })
  })

  return window.aurabookPyodide
}

function loadPyodideScript(): Promise<void> {
  if (window.loadPyodide) {
    return Promise.resolve()
  }

  const existing = document.querySelector<HTMLScriptElement>(`script[src="${PYODIDE_SCRIPT_URL}"]`)
  if (existing) {
    return new Promise((resolve, reject) => {
      existing.addEventListener('load', () => resolve(), { once: true })
      existing.addEventListener('error', () => reject(new Error('Could not load Pyodide.')), { once: true })
    })
  }

  return new Promise((resolve, reject) => {
    const script = document.createElement('script')
    script.src = PYODIDE_SCRIPT_URL
    script.async = true
    script.onload = () => resolve()
    script.onerror = () => reject(new Error('Could not load Pyodide.'))
    document.head.appendChild(script)
  })
}

function looksLikePython(text: string): boolean {
  if (!text || text.length > 4000) {
    return false
  }
  return /(^|\n)\s*(print\(|import\s+\w+|from\s+\w+\s+import|def\s+\w+\(|class\s+\w+|for\s+\w+\s+in\s+|if\s+.+:)/.test(
    text,
  )
}

function stripPlainCode(text: string): string {
  return text
    .replace(/^Here(?:'s| is).+?:\s*/i, '')
    .replace(/^`+|`+$/g, '')
    .trim()
}
