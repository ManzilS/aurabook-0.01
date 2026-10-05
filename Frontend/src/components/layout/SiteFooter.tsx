import { Link } from 'react-router-dom'
import { appConfig } from '../../lib/config'

export function SiteFooter() {
  return (
    <footer className="border-t border-white/6 bg-surface/60">
      <div className="mx-auto flex max-w-7xl flex-col gap-4 px-4 py-8 text-sm text-text-secondary sm:flex-row sm:items-center sm:justify-between sm:px-6 lg:px-8">
        <div className="flex flex-wrap gap-4">
          <a href="/quickstart.html">Get started / Docs</a>
          <a href={appConfig.githubUrl} target="_blank" rel="noreferrer">
            GitHub
          </a>
          <a href={appConfig.linkedinUrl} target="_blank" rel="noreferrer">
            LinkedIn
          </a>
          <Link to="/demo">Demo</Link>
          <Link to="/dashboard">Dashboard</Link>
        </div>
        <p>AuraBook developer preview. Built for developers.</p>
      </div>
    </footer>
  )
}
