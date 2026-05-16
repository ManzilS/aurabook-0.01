import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Building2, Plus } from 'lucide-react'
import { orgApi, type Org } from '../services/orgApi'

export function OrgsListPage() {
  const [orgs, setOrgs] = useState<Org[]>([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [name, setName] = useState('')
  const [slug, setSlug] = useState('')
  const [error, setError] = useState('')
  const navigate = useNavigate()

  useEffect(() => {
    // For now just show create — in future, fetch user's orgs
    setLoading(false)
  }, [])

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    try {
      const org = await orgApi.createOrg(name, slug)
      navigate(`/orgs/${org.slug}`)
    } catch (err: any) {
      setError(err.message)
    }
  }

  const autoSlug = (value: string) => {
    setName(value)
    setSlug(value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40))
  }

  return (
    <div className="mx-auto max-w-3xl px-4 py-16">
      <div className="mb-8 flex items-center justify-between">
        <h1 className="text-2xl font-bold">Organizations</h1>
        <button
          onClick={() => setShowCreate(true)}
          className="flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium hover:bg-indigo-500"
        >
          <Plus size={16} /> New Organization
        </button>
      </div>

      {showCreate && (
        <form onSubmit={handleCreate} className="mb-8 rounded-lg border border-white/10 p-6">
          <h2 className="mb-4 text-lg font-medium">Create Organization</h2>
          {error && <p className="mb-3 text-sm text-red-400">{error}</p>}
          <div className="mb-4">
            <label className="mb-1 block text-sm text-text-secondary">Name</label>
            <input
              type="text"
              value={name}
              onChange={(e) => autoSlug(e.target.value)}
              placeholder="My Team"
              className="w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
              required
            />
          </div>
          <div className="mb-4">
            <label className="mb-1 block text-sm text-text-secondary">Slug (URL-safe)</label>
            <input
              type="text"
              value={slug}
              onChange={(e) => setSlug(e.target.value)}
              placeholder="my-team"
              pattern="^[a-z0-9][a-z0-9-]*[a-z0-9]$"
              className="w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2 font-mono text-sm focus:border-indigo-500 focus:outline-none"
              required
            />
          </div>
          <div className="flex gap-2">
            <button type="submit" className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium hover:bg-indigo-500">
              Create
            </button>
            <button type="button" onClick={() => setShowCreate(false)} className="rounded-lg border border-white/10 px-4 py-2 text-sm hover:bg-white/5">
              Cancel
            </button>
          </div>
        </form>
      )}

      {orgs.length > 0 ? (
        <div className="divide-y divide-white/5 rounded-lg border border-white/10">
          {orgs.map((org) => (
            <Link
              key={org.id}
              to={`/orgs/${org.slug}`}
              className="flex items-center gap-4 px-4 py-4 hover:bg-white/5"
            >
              <Building2 size={20} className="text-indigo-400" />
              <div className="flex-1">
                <p className="font-medium">{org.name}</p>
                <p className="text-xs text-text-secondary">
                  {org.member_count} members &middot; {org.plan} plan
                </p>
              </div>
            </Link>
          ))}
        </div>
      ) : !showCreate ? (
        <div className="rounded-lg border border-dashed border-white/20 p-12 text-center">
          <Building2 size={40} className="mx-auto mb-4 text-text-secondary" />
          <p className="mb-2 text-text-secondary">No organizations yet</p>
          <button
            onClick={() => setShowCreate(true)}
            className="text-sm text-indigo-400 hover:underline"
          >
            Create your first organization
          </button>
        </div>
      ) : null}
    </div>
  )
}
