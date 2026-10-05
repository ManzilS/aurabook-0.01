import { useEffect } from 'react'
import { Link } from 'react-router-dom'
import { CheckCircle2, Circle, XCircle } from 'lucide-react'
import { demoScenarios, livePromptSuggestions } from '../data/scenarios'
import { Button } from '../components/common/Button'
import { SectionHeading } from '../components/common/SectionHeading'
import { TerminalPanel } from '../components/demo/TerminalPanel'
import { useDemoStore } from '../store/demoStore'

export function DemoPage() {
  const {
    mode,
    selectedScenarioId,
    selectedPrompt,
    apiKey,
    rememberApiKey,
    apiKeyStatus,
    apiKeyStatusMessage,
    provider,
    availableModels,
    selectedModel,
    running,
    error,
    mobileVisiblePanel,
    activeScenario,
    left,
    right,
    setMode,
    setScenarioId,
    setSelectedPrompt,
    setApiKey,
    setProvider,
    setRememberApiKey,
    setSelectedModel,
    refreshModelList,
    setMobileVisiblePanel,
    runSelectedScenario,
    runLiveScenario,
    resetPanels,
  } = useDemoStore()

  useEffect(() => {
    resetPanels()
  }, [selectedScenarioId, mode, resetPanels])

  useEffect(() => {
    if (mode === 'live' && useDemoStore.getState().apiKey) {
      void refreshModelList()
    }
  }, [mode, refreshModelList])

  const tokensSaved = Math.max(0, left.tokenCount - right.tokenCount)
  const keyStatusIcon =
    apiKeyStatus === 'valid' ? (
      <CheckCircle2 className="text-success" size={18} />
    ) : apiKeyStatus === 'invalid' ? (
      <XCircle className="text-danger" size={18} />
    ) : (
      <Circle className={apiKeyStatus === 'checking' ? 'text-warning' : 'text-text-secondary'} size={18} />
    )

  return (
    <div className="bg-night">
      <section className="mx-auto max-w-7xl px-4 py-16 sm:px-6 lg:px-8">
        <SectionHeading
          eyebrow="Demo"
          title="Watch AuraBook compress the bug spiral."
          description="Two agents start from the same prompt. One rediscovers the environment trap from scratch. The other starts with the right note already injected."
        />

        <div className="mt-12 space-y-8">
          <div className="flex items-center gap-3 md:hidden">
            <Button
              variant={mobileVisiblePanel === 'left' ? 'primary' : 'secondary'}
              onClick={() => setMobileVisiblePanel('left')}
            >
              Without AuraBook
            </Button>
            <Button
              variant={mobileVisiblePanel === 'right' ? 'primary' : 'secondary'}
              onClick={() => setMobileVisiblePanel('right')}
            >
              With AuraBook
            </Button>
          </div>

          <div className="relative grid gap-6 md:grid-cols-2">
            <div className={mobileVisiblePanel === 'left' ? 'block' : 'hidden md:block'}>
              <TerminalPanel panel={left} />
            </div>
            <div className={mobileVisiblePanel === 'right' ? 'block' : 'hidden md:block'}>
              <TerminalPanel panel={right} />
            </div>
            <div className="pointer-events-none absolute inset-y-0 left-1/2 hidden -translate-x-1/2 items-center md:flex" aria-hidden="true">
              <div className="relative h-full w-px">
                <span className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-full border border-white/10 bg-night px-4 py-2 text-xs uppercase tracking-[0.35em] text-text-secondary">
                  VS
                </span>
              </div>
            </div>
          </div>

          <div className="surface-glass panel-border rounded-[2rem] p-6">
            <div className="flex flex-col gap-6 lg:flex-row lg:items-start lg:justify-between">
              <div className="space-y-2">
                <p className="text-xs uppercase tracking-[0.35em] text-accent">Current scenario</p>
                <h2 className="text-3xl font-semibold text-white">{activeScenario.title}</h2>
                <p className="max-w-3xl text-text-secondary">{activeScenario.rightSummary}</p>
              </div>
              <div className="rounded-[1.5rem] border border-success/20 bg-success/10 px-5 py-4">
                <p className="text-xs uppercase tracking-[0.3em] text-success">Token delta</p>
                <p className="mt-2 text-3xl font-semibold text-white">{tokensSaved.toLocaleString()} saved</p>
              </div>
            </div>

            <div className="mt-8 flex flex-wrap gap-3">
              <Button variant={mode === 'rails' ? 'primary' : 'secondary'} onClick={() => setMode('rails')}>
                Rails Demo
              </Button>
              <Button variant={mode === 'live' ? 'primary' : 'secondary'} onClick={() => setMode('live')}>
                Live Demo
              </Button>
            </div>

            {mode === 'rails' ? (
              <div className="mt-8 grid gap-6 lg:grid-cols-[1fr_auto] lg:items-end">
                <div className="space-y-4">
                  <label className="block text-sm font-medium text-white">
                    Scenario
                    <select
                      value={selectedScenarioId}
                      onChange={(event) => setScenarioId(event.target.value)}
                      className="mt-2 w-full rounded-[1.5rem] border border-white/10 bg-night px-4 py-3 text-sm text-white"
                    >
                      {demoScenarios.map((scenario) => (
                        <option key={scenario.id} value={scenario.id}>
                          {scenario.label}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                <Button size="lg" onClick={() => void runSelectedScenario()} disabled={running}>
                  {running ? 'Running…' : 'Run Demo'}
                </Button>
              </div>
            ) : (
              <div className="mt-8 grid gap-6">
                <label className="block text-sm font-medium text-white">
                  Provider
                  <select aria-label="Provider" value={provider} onChange={(event) => setProvider(event.target.value)} disabled={running}
                    className="mt-2 w-full rounded-lg border border-white/10 bg-night px-4 py-3">
                    <option value="openai">OpenAI</option>
                    <option value="claude">Anthropic Claude</option>
                    <option value="gemini">Google Gemini</option>
                    <option value="deepseek">DeepSeek</option>
                  </select>
                </label>
                <label className="block text-sm font-medium text-white">
                  Prompt
                  <textarea
                    value={selectedPrompt}
                    onChange={(event) => setSelectedPrompt(event.target.value)}
                    placeholder="Ask anything you want both live agents to answer."
                    rows={5}
                    className="mt-2 w-full resize-y rounded-[1.5rem] border border-white/10 bg-night px-4 py-3 text-sm text-white placeholder:text-text-secondary"
                  />
                </label>

                <div className="grid gap-6 lg:grid-cols-[1fr_auto] lg:items-end">
                  <label className="block text-sm font-medium text-white">
                    API key
                    <div className="mt-2 flex items-center gap-3 rounded-[1.5rem] border border-white/10 bg-night px-4 py-3">
                      <input
                        type="password"
                        value={apiKey}
                        onChange={(event) => setApiKey(event.target.value)}
                        placeholder="Enter your OpenAI, Anthropic, or Gemini API key"
                        className="min-w-0 flex-1 bg-transparent text-sm text-white outline-none placeholder:text-text-secondary"
                      />
                      {keyStatusIcon}
                    </div>
                    <span className="mt-2 block text-xs font-normal text-text-secondary">
                      {provider.toUpperCase()} · {apiKeyStatusMessage}
                    </span>
                  </label>
                  <Button size="lg" onClick={() => void runLiveScenario()} disabled={running}>
                    {running ? 'Running…' : 'Run Two Live Calls'}
                  </Button>
                </div>

                <label className="block text-sm font-medium text-white">
                  Model
                  <select
                    value={selectedModel}
                    onChange={(event) => setSelectedModel(event.target.value)}
                    disabled={availableModels.length === 0}
                    className="mt-2 w-full rounded-[1.5rem] border border-white/10 bg-night px-4 py-3 text-sm text-white disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {availableModels.length === 0 ? (
                      <option value="">Validate an API key to load models</option>
                    ) : null}
                    {availableModels.map((model) => (
                      <option key={model} value={model}>
                        {model}
                      </option>
                    ))}
                  </select>
                </label>

                <label className="flex items-start gap-3 text-sm text-text-secondary">
                  <input
                    type="checkbox"
                    checked={rememberApiKey}
                    onChange={(event) => setRememberApiKey(event.target.checked)}
                    className="mt-1 h-4 w-4 rounded border-white/20 bg-night"
                  />
                  <span>
                    Remember this API key in this browser. When off, the key is kept only for this tab session.
                  </span>
                </label>

                <div className="rounded-2xl border border-white/10 bg-white/[0.03] px-4 py-3 text-sm text-text-secondary">
                  Your key is sent over HTTPS to the AuraBook backend, which forwards requests to your selected provider. It does not stay only in your browser. The demo does not write your key to the database. Browser storage is controlled by the remember option above.{' '}
                  Live mode uses your key for retrieval when the provider supports embeddings, then makes two real
                  completion calls: baseline without AuraBook and AuraBook with retrieved context. Those calls should
                  appear in your provider usage logs. Python code blocks in the responses run locally in Pyodide.
                </div>

                <div className="flex flex-wrap gap-3">
                  {livePromptSuggestions.map((prompt) => (
                    <button
                      key={prompt}
                      type="button"
                      onClick={() => setSelectedPrompt(prompt)}
                      className={`rounded-full border px-4 py-2 text-sm transition ${
                        selectedPrompt === prompt
                          ? 'border-accent/40 bg-accent/10 text-white'
                          : 'border-white/10 bg-white/5 text-text-secondary hover:text-white'
                      }`}
                    >
                      {prompt}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {error ? <p className="mt-4 text-sm text-danger">{error}</p> : null}
          </div>

          <div className="flex flex-wrap items-center justify-between gap-4 text-sm text-text-secondary">
            <span>{activeScenario.leftSummary}</span>
            <Link to="/dashboard" className="shrink-0 rounded-lg border border-accent/40 px-4 py-2 text-accent underline underline-offset-4">
              Explore the live knowledge feed
            </Link>
          </div>
        </div>
      </section>
    </div>
  )
}
