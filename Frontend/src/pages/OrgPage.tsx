import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { Users, Trophy, Settings, Crown, Shield, Eye, User } from 'lucide-react'
import { orgApi, type Org, type Member, type LeaderboardEntry } from '../services/orgApi'

const ROLE_ICONS: Record<string, typeof Crown> = {
  owner: Crown,
  admin: Shield,
  member: User,
  viewer: Eye,
}

export function OrgPage() {
  const { slug } = useParams<{ slug: string }>()
  const [org, setOrg] = useState<Org | null>(null)
  const [members, setMembers] = useState<Member[]>([])
  const [leaderboard, setLeaderboard] = useState<LeaderboardEntry[]>([])
  const [tab, setTab] = useState<'members' | 'leaderboard' | 'settings'>('members')
  const [error, setError] = useState('')
  const [inviteEmail, setInviteEmail] = useState('')
  const [inviteRole, setInviteRole] = useState('member')

  useEffect(() => {
    if (!slug) return
    orgApi.getOrg(slug).then(setOrg).catch((e) => setError(e.message))
    orgApi.listMembers(slug).then(setMembers).catch(() => {})
    orgApi.leaderboard(slug).then(setLeaderboard).catch(() => {})
  }, [slug])

  const handleInvite = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!slug || !inviteEmail) return
    try {
      const member = await orgApi.addMember(slug, inviteEmail, inviteRole)
      setMembers([...members, member])
      setInviteEmail('')
    } catch (err: any) {
      setError(err.message)
    }
  }

  const handleRemove = async (userId: string) => {
    if (!slug) return
    try {
      await orgApi.removeMember(slug, userId)
      setMembers(members.filter((m) => m.user_id !== userId))
    } catch (err: any) {
      setError(err.message)
    }
  }

  if (error && !org) {
    return (
      <div className="mx-auto max-w-4xl px-4 py-16 text-center">
        <p className="text-red-400">{error}</p>
        <Link to="/orgs" className="mt-4 text-indigo-400 hover:underline">Back to organizations</Link>
      </div>
    )
  }

  if (!org) {
    return <div className="mx-auto max-w-4xl px-4 py-16 text-center text-text-secondary">Loading...</div>
  }

  return (
    <div className="mx-auto max-w-5xl px-4 py-8">
      {/* Header */}
      <div className="mb-8 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">{org.name}</h1>
          <p className="text-text-secondary">
            {org.member_count} members &middot; {org.trajectory_count} trajectories &middot;{' '}
            <span className="rounded bg-indigo-900/50 px-2 py-0.5 text-xs font-medium text-indigo-300">
              {org.plan}
            </span>
          </p>
        </div>
      </div>

      {/* Tabs */}
      <div className="mb-6 flex gap-1 rounded-lg bg-white/5 p-1">
        {[
          { key: 'members' as const, label: 'Members', icon: Users },
          { key: 'leaderboard' as const, label: 'Leaderboard', icon: Trophy },
          { key: 'settings' as const, label: 'Settings', icon: Settings },
        ].map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium transition ${
              tab === key ? 'bg-white/10 text-white' : 'text-text-secondary hover:text-white'
            }`}
          >
            <Icon size={16} /> {label}
          </button>
        ))}
      </div>

      {error && <p className="mb-4 rounded bg-red-900/30 px-3 py-2 text-sm text-red-300">{error}</p>}

      {/* Members tab */}
      {tab === 'members' && (
        <div className="space-y-4">
          <form onSubmit={handleInvite} className="flex gap-2">
            <input
              type="email"
              placeholder="Invite by email..."
              value={inviteEmail}
              onChange={(e) => setInviteEmail(e.target.value)}
              className="flex-1 rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
            />
            <select
              value={inviteRole}
              onChange={(e) => setInviteRole(e.target.value)}
              className="rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm"
            >
              <option value="viewer">Viewer</option>
              <option value="member">Member</option>
              <option value="admin">Admin</option>
            </select>
            <button
              type="submit"
              className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium hover:bg-indigo-500"
            >
              Invite
            </button>
          </form>

          <div className="divide-y divide-white/5 rounded-lg border border-white/10">
            {members.map((m) => {
              const RoleIcon = ROLE_ICONS[m.role] || User
              return (
                <div key={m.user_id} className="flex items-center justify-between px-4 py-3">
                  <div className="flex items-center gap-3">
                    <RoleIcon size={16} className="text-text-secondary" />
                    <div>
                      <p className="text-sm font-medium">{m.display_name}</p>
                      <p className="text-xs text-text-secondary">{m.email}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="text-xs text-text-secondary">{m.role}</span>
                    {m.role !== 'owner' && (
                      <button
                        onClick={() => handleRemove(m.user_id)}
                        className="text-xs text-red-400 hover:text-red-300"
                      >
                        Remove
                      </button>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Leaderboard tab */}
      {tab === 'leaderboard' && (
        <div className="rounded-lg border border-white/10">
          <table className="w-full text-sm">
            <thead className="border-b border-white/10 text-text-secondary">
              <tr>
                <th className="px-4 py-3 text-left font-medium">#</th>
                <th className="px-4 py-3 text-left font-medium">Developer</th>
                <th className="px-4 py-3 text-right font-medium">Trajectories</th>
                <th className="px-4 py-3 text-right font-medium">Helpful Votes</th>
                <th className="px-4 py-3 text-right font-medium">Tokens Saved</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {leaderboard.map((entry, i) => (
                <tr key={entry.user_id} className="hover:bg-white/5">
                  <td className="px-4 py-3 font-mono text-text-secondary">{i + 1}</td>
                  <td className="px-4 py-3 font-medium">{entry.display_name}</td>
                  <td className="px-4 py-3 text-right">{entry.trajectories_contributed}</td>
                  <td className="px-4 py-3 text-right text-green-400">{entry.helpful_votes_received}</td>
                  <td className="px-4 py-3 text-right text-indigo-400">
                    {(entry.tokens_saved_estimate / 1000).toFixed(1)}k
                  </td>
                </tr>
              ))}
              {leaderboard.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-4 py-8 text-center text-text-secondary">
                    No contributions yet. Start ingesting trajectories!
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* Settings tab */}
      {tab === 'settings' && (
        <div className="space-y-6">
          <div className="rounded-lg border border-white/10 p-6">
            <h3 className="mb-2 font-medium">Plan</h3>
            <p className="mb-4 text-sm text-text-secondary">
              Current plan: <strong className="text-white">{org.plan}</strong>
            </p>
            {org.plan === 'free' && (
              <div className="flex gap-2">
                {['starter', 'team', 'enterprise'].map((plan) => (
                  <button
                    key={plan}
                    onClick={async () => {
                      try {
                        const { checkout_url } = await orgApi.createCheckout(slug!, plan)
                        window.location.href = checkout_url
                      } catch (err: any) {
                        setError(err.message)
                      }
                    }}
                    className="rounded-lg border border-white/10 px-4 py-2 text-sm capitalize hover:bg-white/5"
                  >
                    Upgrade to {plan}
                  </button>
                ))}
              </div>
            )}
            {org.plan !== 'free' && (
              <button
                onClick={async () => {
                  try {
                    const { portal_url } = await orgApi.createPortal(slug!)
                    window.location.href = portal_url
                  } catch (err: any) {
                    setError(err.message)
                  }
                }}
                className="rounded-lg border border-white/10 px-4 py-2 text-sm hover:bg-white/5"
              >
                Manage subscription
              </button>
            )}
          </div>

          <div className="rounded-lg border border-white/10 p-6">
            <h3 className="mb-2 font-medium">API Key</h3>
            <p className="text-sm text-text-secondary">
              Use your org's API key in the AuraBook client to scope trajectories to this organization.
            </p>
            <code className="mt-2 block rounded bg-white/5 px-3 py-2 text-xs text-text-secondary">
              AURABOOK_CLOUD_API_KEY=ab_...
            </code>
          </div>
        </div>
      )}
    </div>
  )
}
