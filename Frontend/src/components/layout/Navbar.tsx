import { Menu, X } from 'lucide-react'
import { NavLink } from 'react-router-dom'
import { appConfig } from '../../lib/config'
import { cn } from '../../lib/cn'
import { useAppStore } from '../../store/appStore'
import { ConnectionStatus } from './ConnectionStatus'

const navItems = [
  { label: 'Demo', to: '/demo' },
  { label: 'Dashboard', to: '/dashboard' },
]

export function Navbar() {
  const mobileMenuOpen = useAppStore((state) => state.mobileMenuOpen)
  const setMobileMenuOpen = useAppStore((state) => state.setMobileMenuOpen)

  return (
    <header className="fixed inset-x-0 top-0 z-50 border-b border-white/6 bg-night/80 backdrop-blur-xl">
      <div className="mx-auto flex h-20 max-w-7xl items-center justify-between px-4 sm:px-6 lg:px-8">
        <NavLink to="/" className="flex items-center gap-3" onClick={() => setMobileMenuOpen(false)}>
          <span className="flex h-10 w-10 items-center justify-center rounded-2xl border border-accent/30 bg-accent/10">
            <span className="h-4 w-4 rounded-full bg-accent shadow-glow" />
          </span>
          <div>
            <p className="text-lg font-semibold tracking-tight text-white">AuraBook</p>
            <p className="text-xs uppercase tracking-[0.3em] text-text-secondary">Shared agent memory</p>
          </div>
        </NavLink>

        <nav className="hidden items-center gap-6 md:flex">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                cn('text-sm text-text-secondary transition hover:text-white', isActive && 'text-white')
              }
            >
              {item.label}
            </NavLink>
          ))}
          <a
            href={appConfig.githubUrl}
            target="_blank"
            rel="noreferrer"
            className="text-sm text-text-secondary transition hover:text-white"
          >
            GitHub
          </a>
          <a
            href={appConfig.linkedinUrl}
            target="_blank"
            rel="noreferrer"
            className="text-sm text-text-secondary transition hover:text-white"
          >
            LinkedIn
          </a>
          <ConnectionStatus />
        </nav>

        <button
          type="button"
          className="inline-flex h-11 w-11 items-center justify-center rounded-full border border-white/10 bg-white/5 text-white md:hidden"
          onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
          aria-label="Toggle navigation"
        >
          {mobileMenuOpen ? <X size={18} /> : <Menu size={18} />}
        </button>
      </div>

      {mobileMenuOpen ? (
        <div className="border-t border-white/6 bg-surface/95 px-4 py-4 md:hidden">
          <div className="flex flex-col gap-4">
            {navItems.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className="text-sm text-text-secondary transition hover:text-white"
                onClick={() => setMobileMenuOpen(false)}
              >
                {item.label}
              </NavLink>
            ))}
            <a href={appConfig.githubUrl} target="_blank" rel="noreferrer" className="text-sm text-text-secondary">
              GitHub
            </a>
            <a href={appConfig.linkedinUrl} target="_blank" rel="noreferrer" className="text-sm text-text-secondary">
              LinkedIn
            </a>
          </div>
        </div>
      ) : null}
    </header>
  )
}
