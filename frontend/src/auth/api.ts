export interface SessionUser {
  id: string
  email: string
  name: string
  avatar_url: string | null
  country: string | null
  base_currency: string
  status: string
  created_at: string
  updated_at: string
}
export const apiBase = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')
export const googleLoginUrl = `${apiBase}/api/auth/google`
export async function readSession(signal?: AbortSignal): Promise<SessionUser | null> {
  const response = await fetch(`${apiBase}/api/auth/me`, { credentials: 'include', signal, cache: 'no-store' })
  if (response.status === 401) return null
  if (!response.ok) throw new Error('Your sign-in could not be checked. Please retry.')
  return response.json()
}
export async function logout() {
  const response = await fetch(`${apiBase}/api/auth/logout`, { method: 'POST', credentials: 'include' })
  if (!response.ok) throw new Error('Sign-out could not be completed. Please try again.')
}