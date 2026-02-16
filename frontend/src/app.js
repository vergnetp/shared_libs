/**
 * app.js — App initialization + authentication workflows
 *
 * Wires JWT auth into the API client and orchestrates login, register,
 * logout, and session initialization. Clears SWR cache on logout.
 *
 * Call `initApp()` once on app mount to configure auth + validate session.
 *
 * @example
 *   // App.svelte or main.js
 *   import { initApp } from '@myorg/ui'
 *   await initApp()
 *
 *   // Login form
 *   import { login } from '@myorg/ui'
 *   const user = await login(email, password)
 *
 *   // Sign out
 *   import { logout } from '@myorg/ui'
 *   <button on:click={logout}>Sign out</button>
 *
 * @module app
 */
import { get } from 'svelte/store'
import { useAuth } from './hooks/auth.js'
import { api } from './api/client.js'
import { clearSWRCache } from './api/swr.js'

// =============================================================================
// JWT Auth Handler (injected into api client)
// =============================================================================

function isValidJwtFormat(token) {
  if (!token || typeof token !== 'string') return false
  return token.split('.').length === 3
}

/**
 * Auth handler for api.configure(). Reads token from auth store,
 * validates JWT format, skips auth endpoints.
 *
 * @param {string} path - Request path
 * @returns {{ headers: Object }|{ skip: string }|null}
 */
function jwtAuth(path) {
  const isAuthEndpoint = path.startsWith('/auth/login') || path.startsWith('/auth/register')
  if (isAuthEndpoint) return null

  const auth = get(useAuth)
  if (!auth.token) return null

  if (!isValidJwtFormat(auth.token)) {
    console.error('Invalid JWT format detected - token may be corrupted')
    useAuth.logout()
    return { skip: 'Session corrupted - please login again' }
  }

  return { headers: { 'Authorization': `Bearer ${auth.token}` } }
}

/**
 * 401 handler. Expires session and clears SWR cache.
 *
 * @param {string} msg - Error message from server
 */
function handleUnauthorized(msg) {
  const auth = get(useAuth)
  if (auth.token) {
    useAuth.logout()
    clearSWRCache()
  }
}

// =============================================================================
// App Initialization
// =============================================================================

/**
 * Initialize the app: configure API auth + validate persisted session.
 * Call once on app mount (App.svelte onMount or main.js).
 *
 * @param {Object} [options={}]
 * @param {string} [options.baseUrl='/api/v1'] - API base URL
 * @returns {Promise<boolean>} true if user session is valid
 *
 * @example
 *   import { initApp } from '@myorg/ui'
 *
 *   // Simple
 *   await initApp()
 *
 *   // Custom base URL
 *   await initApp({ baseUrl: '/api/v2' })
 */
export async function initApp(options = {}) {
  api.configure({
    baseUrl: options.baseUrl || '/api/v1',
    auth: jwtAuth,
    onUnauthorized: handleUnauthorized,
    ...options,
  })

  return initAuth()
}

// =============================================================================
// Auth Flows
// =============================================================================

/**
 * Login with email and password. Sets auth token and fetches user profile.
 *
 * @param {string} email
 * @param {string} password
 * @returns {Promise<Object>} User object from /auth/me
 * @throws {Error} On invalid credentials or network failure
 */
export async function login(email, password) {
  useAuth.setLoading(true)
  useAuth.clearError()

  try {
    const res = await api('POST', '/auth/login', { username: email, password })
    useAuth.setToken(res.access_token)

    const user = await api('GET', '/auth/me')
    useAuth.setUser(user)
    useAuth.setLoading(false)

    return user
  } catch (err) {
    useAuth.setError(err.message)
    throw err
  }
}

/**
 * Register a new account, then auto-login.
 *
 * @param {string} email
 * @param {string} password
 * @returns {Promise<Object>} User object from /auth/me
 * @throws {Error} On registration failure or network error
 */
export async function register(email, password) {
  useAuth.setLoading(true)
  useAuth.clearError()

  try {
    await api('POST', '/auth/register', { username: email, email, password })
    return await login(email, password)
  } catch (err) {
    useAuth.setError(err.message)
    throw err
  }
}

/**
 * Logout: clear auth state + SWR cache.
 * Ensures no stale data from the previous session persists.
 *
 * @example
 *   import { logout } from '@myorg/ui'
 *   <button on:click={logout}>Sign out</button>
 */
export function logout() {
  useAuth.logout()
  clearSWRCache()
}

/**
 * Initialize auth from persisted token (cookie/localStorage).
 * Validates the token by calling /auth/me. Logs out on failure.
 *
 * @returns {Promise<boolean>} true if token was valid and user was loaded
 */
export async function initAuth() {
  const auth = get(useAuth)

  if (!auth.token || auth.token.trim() === '') {
    return false
  }

  try {
    const user = await api('GET', '/auth/me')
    useAuth.setUser(user)
    return true
  } catch (err) {
    logout()
    return false
  }
}