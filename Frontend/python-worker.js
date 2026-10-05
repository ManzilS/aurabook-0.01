/* global importScripts, loadPyodide */
self.onmessage = async ({ data }) => {
  try {
    const indexURL = 'https://cdn.jsdelivr.net/pyodide/v0.27.7/full/'
    importScripts(`${indexURL}pyodide.js`)
    const runtime = await loadPyodide({ indexURL })
    runtime.globals.set('_aurabook_code', data.code)
    self.postMessage({ type: 'ready' })
    const raw = await runtime.runPythonAsync(`
import contextlib, io, json, traceback
out, err = io.StringIO(), io.StringIO()
failure = None
with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
    try:
        exec(_aurabook_code, {"__name__": "__main__"})
    except BaseException:
        failure = traceback.format_exc()
json.dumps({"stdout": out.getvalue()[:100000], "stderr": err.getvalue()[:100000], "error": failure})
`)
    self.postMessage({ type: 'result', result: JSON.parse(raw) })
  } catch (error) {
    self.postMessage({ type: 'result', result: { stdout: '', stderr: '', error: String(error) } })
  }
}
