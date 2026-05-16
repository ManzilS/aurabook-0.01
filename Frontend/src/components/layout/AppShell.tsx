import { Suspense, lazy } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { Navbar } from './Navbar'

const LandingPage = lazy(async () => import('../../pages/LandingPage').then((module) => ({ default: module.LandingPage })))
const DemoPage = lazy(async () => import('../../pages/DemoPage').then((module) => ({ default: module.DemoPage })))
const DashboardPage = lazy(async () =>
  import('../../pages/DashboardPage').then((module) => ({ default: module.DashboardPage })),
)
const NoteIssuePage = lazy(async () =>
  import('../../pages/NoteIssuePage').then((module) => ({ default: module.NoteIssuePage })),
)
const ErrorIssuePage = lazy(async () =>
  import('../../pages/ErrorIssuePage').then((module) => ({ default: module.ErrorIssuePage })),
)
const OrgsListPage = lazy(async () =>
  import('../../pages/OrgsListPage').then((module) => ({ default: module.OrgsListPage })),
)
const OrgPage = lazy(async () =>
  import('../../pages/OrgPage').then((module) => ({ default: module.OrgPage })),
)

export function AppShell() {
  const location = useLocation()

  return (
    <div className="h-screen overflow-hidden bg-night text-white">
      <Navbar />
      <main className="h-screen overflow-x-hidden overflow-y-auto pt-20">
        <div key={location.pathname} className="animate-page-in overflow-x-hidden">
          <Suspense
            fallback={
              <div className="mx-auto flex min-h-[60vh] max-w-7xl items-center justify-center px-4 text-text-secondary">
                Loading AuraBook…
              </div>
            }
          >
            <Routes location={location}>
              <Route path="/" element={<LandingPage />} />
              <Route path="/demo" element={<DemoPage />} />
              <Route path="/dashboard" element={<DashboardPage />} />
              <Route path="/orgs" element={<OrgsListPage />} />
              <Route path="/orgs/:slug" element={<OrgPage />} />
              <Route path="/errors" element={<Navigate to="/dashboard" replace />} />
              <Route path="/errors/:clusterId" element={<ErrorIssuePage />} />
              <Route path="/notes/:noteId" element={<NoteIssuePage />} />
            </Routes>
          </Suspense>
        </div>
      </main>
    </div>
  )
}
