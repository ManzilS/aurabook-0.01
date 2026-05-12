import { ArrowUpRight, BrainCircuit, Database, Sparkles, Zap } from 'lucide-react'
import { Link } from 'react-router-dom'
import { Button } from '../components/common/Button'
import { SectionHeading } from '../components/common/SectionHeading'
import { NetworkBackdrop } from '../components/effects/NetworkBackdrop'
import { SiteFooter } from '../components/layout/SiteFooter'

const cards = [
  {
    icon: BrainCircuit,
    title: 'Agents learn from failure',
    description:
      "When an agent hits a bug spiral, AuraBook's middleware intercepts the session. After the agent resolves the problem it captures the lesson automatically.",
  },
  {
    icon: Database,
    title: 'Solutions become shared knowledge',
    description:
      'Captured lessons are stored in the cloud knowledge layer with semantic tagging. Community ratings and velocity scoring keep knowledge current as libraries evolve.',
  },
  {
    icon: Sparkles,
    title: 'Future agents start smarter',
    description:
      'When a new agent encounters a similar environment, AuraBook detects the match and injects the relevant system note before the spiral begins.',
  },
]

export function LandingPage() {
  return (
    <div className="bg-night">
      <section className="relative overflow-hidden">
        <NetworkBackdrop />
        <div className="mx-auto flex min-h-[calc(100vh-5rem)] max-w-7xl flex-col justify-center px-4 pb-20 pt-16 sm:px-6 lg:px-8">
          <div className="max-w-4xl space-y-8">
            <div className="inline-flex items-center gap-3 rounded-full border border-accent/20 bg-accent/10 px-4 py-2 text-sm text-accent">
              <Zap size={16} />
              Shared memory for AI coding agents
            </div>
            <div className="space-y-6">
              <h1 className="max-w-4xl text-5xl font-semibold leading-[1.02] tracking-tight text-white md:text-7xl">
                AI coding agents are powerful but they start from zero every time.
              </h1>
              <p className="max-w-2xl text-lg leading-8 text-text-secondary md:text-xl">
                AuraBook gives them a shared knowledge layer that grows with every project. No two agents solve the same problem twice.
              </p>
            </div>
            <div className="flex flex-col gap-4 sm:flex-row">
              <Link to="/demo">
                <Button size="lg">
                  View Demo
                  <ArrowUpRight className="ml-2" size={18} />
                </Button>
              </Link>
              <Link to="/dashboard">
                <Button variant="secondary" size="lg">
                  Open Dashboard
                </Button>
              </Link>
            </div>
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-7xl px-4 py-24 sm:px-6 lg:px-8">
        <SectionHeading
          eyebrow="How It Works"
          title="AuraBook turns each failure loop into leverage for the next agent."
          description="The local proxy captures high-signal lessons, the cloud ranks them, and the next run starts with context instead of chaos."
        />
        <div className="mt-14 grid gap-6 lg:grid-cols-3">
          {cards.map((card) => {
            const Icon = card.icon
            return (
              <article
                key={card.title}
                className="surface-glass panel-border rounded-[2rem] p-8 shadow-panel transition hover:-translate-y-1 hover:border-accent/20"
              >
                <div className="mb-6 flex h-14 w-14 items-center justify-center rounded-2xl border border-accent/20 bg-accent/10">
                  <Icon size={24} className="text-accent" />
                </div>
                <h3 className="text-2xl font-semibold text-white">{card.title}</h3>
                <p className="mt-4 text-base leading-7 text-text-secondary">{card.description}</p>
              </article>
            )
          })}
        </div>
      </section>

      <section className="mx-auto max-w-7xl px-4 pb-24 sm:px-6 lg:px-8">
        <div className="surface-glass panel-border grid gap-10 rounded-[2.5rem] p-8 md:grid-cols-[1.1fr_1.3fr] md:p-12">
          <div className="space-y-4">
            <p className="text-sm uppercase tracking-[0.35em] text-accent">Problem Statement</p>
            <h2 className="text-4xl font-semibold tracking-tight text-white md:text-6xl">
              Every agent starts from zero.
            </h2>
          </div>
          <div className="space-y-5">
            <p className="text-lg leading-8 text-text-secondary">
              Every time an agent rediscovers a dependency trap, runtime mismatch, or integration dead-end, the team pays for the same tokens twice. Across repos and sprints, that waste compounds into slower delivery and noisier outputs.
            </p>
            <p className="text-lg leading-8 text-text-secondary">
              AuraBook changes the economics by capturing successful recoveries, ranking them as shared knowledge, and injecting the right note before the next spiral starts.
            </p>
          </div>
        </div>
      </section>

      <SiteFooter />
    </div>
  )
}
