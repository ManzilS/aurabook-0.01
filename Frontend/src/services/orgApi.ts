/**
 * API client for organization management.
 */

import { appConfig } from '../lib/config'

const API = () => appConfig.dashboardApiUrl?.replace(/\/v1\/dashboard\/?$/, '') || ''

export interface Org {
  id: string
  name: string
  slug: string
  plan: string
  member_count: number
  trajectory_count: number
  created_at: string | null
}

export interface Member {
  user_id: string
  email: string
  display_name: string
  role: string
}

export interface LeaderboardEntry {
  user_id: string
  display_name: string
  trajectories_contributed: number
  helpful_votes_received: number
  tokens_saved_estimate: number
}

export interface UserInfo {
  user_id: string
  email: string
  display_name: string
  avatar_url: string | null
  auth_provider: string
}

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(`${API()}${path}`, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({ error: resp.statusText }))
    throw new Error(body.error || `API error ${resp.status}`)
  }
  return resp.json()
}

export const orgApi = {
  // Auth
  me: () => apiFetch<UserInfo>('/auth/me'),
  loginUrl: () => `${API()}/auth/github`,
  logout: () => apiFetch<{ status: string }>('/auth/logout', { method: 'POST' }),

  // Orgs
  createOrg: (name: string, slug: string) =>
    apiFetch<Org>('/v1/orgs', { method: 'POST', body: JSON.stringify({ name, slug }) }),
  getOrg: (slug: string) => apiFetch<Org>(`/v1/orgs/${slug}`),

  // Members
  listMembers: (slug: string) => apiFetch<Member[]>(`/v1/orgs/${slug}/members`),
  addMember: (slug: string, email: string, role: string) =>
    apiFetch<Member>(`/v1/orgs/${slug}/members`, {
      method: 'POST',
      body: JSON.stringify({ email, role }),
    }),
  removeMember: (slug: string, userId: string) =>
    apiFetch<{ status: string }>(`/v1/orgs/${slug}/members/${userId}`, { method: 'DELETE' }),

  // Leaderboard
  leaderboard: (slug: string, limit = 20) =>
    apiFetch<LeaderboardEntry[]>(`/v1/orgs/${slug}/leaderboard?limit=${limit}`),

  // Billing
  createCheckout: (slug: string, plan: string) =>
    apiFetch<{ checkout_url: string }>('/v1/billing/checkout', {
      method: 'POST',
      body: JSON.stringify({ slug, plan }),
    }),
  createPortal: (slug: string) =>
    apiFetch<{ portal_url: string }>('/v1/billing/portal', {
      method: 'POST',
      body: JSON.stringify({ slug }),
    }),
}
