/**
 * client.js - Shared API client
 *
 * Architecture: ONE core function (coreFetch) calls fetch().
 * All safeguards live there: auth, JWT validation, requestModifier,
 * retry with exponential backoff, error handling.
 *
 * Everything else is a thin wrapper over coreFetch:
 *   api()               → coreFetch + JSON parse
 *   apiRaw()            → coreFetch (returns raw Response)
 *   apiStream()         → coreFetch + SSE reader (no retry)
 *   apiStreamMultipart()→ coreFetch + SSE reader (FormData, no retry)
 *
 * Usage:
 *   import { api, apiStream, setApiConfig } from '@myorg/ui'
 *
 *   setApiConfig({ baseUrl: '/api/v1' })
 *   setApiConfig({
 *     requestModifier(url, path, headers, options) {
 *       if (path.startsWith('/infra/')) {
 *         const token = getMyToken()
 *         if (!token) return { error: 'Token required' }
 *         return { url: url + `?token=${token}`, headers }
 *       }
 *       return { url, headers }
 *     }
 *   })
 *
 *   const data = await api('GET', '/users')
 *   const data = await api('POST', '/users', { name: 'John' })
 */
import { get } from 'svelte/store'
import { authStore, getAuthToken } from '../stores/auth.js'

// =============================================================================
// Configuration
// =============================================================================

let config = {
  baseUrl: '/api/v1',
  onUnauthorized: null,   // Callback when 401 received
  requestModifier: null,  // (url, path, headers, options) => { url, headers } | { error }
}

export function setApiConfig(newConfig) {
  config = { ...config, ...newConfig }
}

export function createApiClient(customConfig = {}) {
  const clientConfig = { ...config, ...customConfig }

  return {
    get: (path, options) => api('GET', path, null, { ...options, config: clientConfig }),
    post: (path, data, options) => api('POST', path, data, { ...options, config: clientConfig }),
    put: (path, data, options) => api('PUT', path, data, { ...options, config: clientConfig }),
    delete: (path, options) => api('DELETE', path, null, { ...options, config: clientConfig }),
    stream: (method, path, data, onMessage, options) =>
      apiStream(method, path, data, onMessage, { ...options, config: clientConfig }),
  }
}

// =============================================================================
// Helpers
// =============================================================================

function isValidJwtFormat(token) {
  if (!token || typeof token !== 'string') return false
  return token.split('.').length === 3
}

function apiError(message, status) {
  const err = new Error(message)
  err.status = status
  return err
}

// =============================================================================
// Error Handler
// =============================================================================

async function handleErrorResponse(res, options = {}) {
  const cfg = options.config || config

  if (res.status === 401) {
    let errorMsg = 'Authentication failed'
    try {
      const errBody = await res.json()
      errorMsg = errBody.detail || errBody.error || errBody.message || errorMsg
    } catch {}

    const auth = get(authStore)
    if (auth.token) {
      authStore.logout()
      errorMsg = 'Session expired - please login again'
    }

    if (cfg.onUnauthorized) {
      cfg.onUnauthorized(errorMsg)
    }

    throw apiError(errorMsg, 401)
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Request failed' }))
    throw apiError(err.detail || err.error || err.message || 'Request failed', res.status)
  }
}

// =============================================================================
// Retry Logic
// =============================================================================

function isRetryable(error) {
  if (error.name === 'TypeError') return true          // network error
  if (error.status && error.status >= 500) return true  // server error
  return false
}

const RETRY_DEFAULTS = { GET: 3, POST: 1, PUT: 1, DELETE: 1 }

async function withRetry(fn, method, options = {}) {
  const maxRetries = options.retries ?? RETRY_DEFAULTS[method] ?? 1
  const baseDelay = 1000

  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try {
      return await fn()
    } catch (err) {
      const last = attempt >= maxRetries
      if (last || !isRetryable(err)) throw err
      await new Promise(r => setTimeout(r, baseDelay * 2 ** attempt))
    }
  }
}

// =============================================================================
// SSE Stream Reader
// =============================================================================

async function readSSE(res, onMessage) {
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''

    for (const line of lines) {
      if (line.startsWith('data: ')) {
        try {
          onMessage(JSON.parse(line.slice(6)))
        } catch {}
      }
    }
  }
}

// =============================================================================
// coreFetch — the ONE function that calls fetch(). All safeguards live here.
//
// Every request in this client goes through this function. It handles:
//   1. Auth (JWT validation + Bearer header)
//   2. requestModifier (app-specific URL/header rewriting)
//   3. Retry with exponential backoff (unless noRetry)
//   4. Error handling (4xx/5xx → thrown errors with .status)
// =============================================================================

async function coreFetch(method, path, {
  data = null,
  noRetry = false,        // true for streaming (don't retry mid-stream)
  throwOnSkip = false,    // true for stream/raw (throw vs return null on skip)
  config: cfg,
  ...options
} = {}) {
  cfg = cfg || config
  const auth = get(authStore)

  // --- Auth + JWT validation ---
  const isAuthEndpoint = path.startsWith('/auth/login') || path.startsWith('/auth/register')

  if (auth.token && !isAuthEndpoint && !options.skipAuth) {
    if (!isValidJwtFormat(auth.token)) {
      console.error('Invalid JWT format detected - token may be corrupted')
      authStore.logout()
      const msg = 'Session corrupted - please login again'
      if (throwOnSkip) throw new Error(msg)
      console.debug(`Skipping ${path} - ${msg}`)
      return null
    }
  }

  // --- Headers ---
  const headers = {}
  if (data instanceof FormData) {
    // Don't set Content-Type — browser sets multipart boundary
  } else {
    headers['Content-Type'] = 'application/json'
  }
  if (auth.token && !isAuthEndpoint && !options.skipAuth) {
    headers['Authorization'] = `Bearer ${auth.token}`
  }
  if (options.headers) Object.assign(headers, options.headers)

  // --- URL + requestModifier ---
  let url = cfg.baseUrl + path

  if (cfg.requestModifier) {
    const modified = cfg.requestModifier(url, path, headers, options)
    if (modified?.error) {
      if (throwOnSkip) throw new Error(modified.error)
      console.debug(`Skipping ${path} - ${modified.error}`)
      return null
    }
    if (modified?.url) url = modified.url
    if (modified?.headers) Object.assign(headers, modified.headers)
  }

  // --- Body ---
  const fetchOpts = { method, headers }
  if (data) {
    fetchOpts.body = data instanceof FormData ? data : JSON.stringify(data)
  }

  // --- Fetch (with or without retry) ---
  if (noRetry) {
    const res = await fetch(url, fetchOpts)
    await handleErrorResponse(res, { ...options, config: cfg })
    return res
  }

  return withRetry(async () => {
    const res = await fetch(url, fetchOpts)
    await handleErrorResponse(res, { ...options, config: cfg })
    return res
  }, method, options)
}

// =============================================================================
// Public API — thin wrappers over coreFetch
// =============================================================================

/** JSON API call. Returns parsed JSON (or unwrapped .data). */
export async function api(method, path, data = null, options = {}) {
  const res = await coreFetch(method, path, { data, ...options })
  if (res === null) return null
  if (res.status === 204) return null

  const result = await res.json()

  // Unwrap common response formats
  if (result && result.success === true && result.data !== undefined) {
    return result.data
  }
  return result
}

/** Raw API call. Returns the raw Response (for blobs, file downloads, etc.). */
export async function apiRaw(method, path, data = null, options = {}) {
  return coreFetch(method, path, { data, throwOnSkip: true, ...options })
}

/** SSE streaming call. Reads the stream and calls onMessage for each event. */
export async function apiStream(method, path, data, onMessage, options = {}) {
  const res = await coreFetch(method, path, { data, noRetry: true, throwOnSkip: true, ...options })
  await readSSE(res, onMessage)
}

/** Multipart SSE streaming (file upload + SSE response). */
export async function apiStreamMultipart(path, formData, onMessage, options = {}) {
  const res = await coreFetch('POST', path, { data: formData, noRetry: true, throwOnSkip: true, ...options })
  await readSSE(res, onMessage)
}

// Convenience methods
export const get_ = (path, options) => api('GET', path, null, options)
export const post = (path, data, options) => api('POST', path, data, options)
export const put = (path, data, options) => api('PUT', path, data, options)
export const del = (path, options) => api('DELETE', path, null, options)

// =============================================================================
// Auth Flows (app_kernel standard endpoints)
// =============================================================================

export async function login(email, password) {
  authStore.setLoading(true)
  authStore.clearError()

  try {
    const res = await api('POST', '/auth/login', { username: email, password })
    authStore.setToken(res.access_token)

    const user = await api('GET', '/auth/me')
    authStore.setUser(user)
    authStore.setLoading(false)

    return user
  } catch (err) {
    authStore.setError(err.message)
    throw err
  }
}

export async function register(email, password) {
  authStore.setLoading(true)
  authStore.clearError()

  try {
    await api('POST', '/auth/register', { username: email, email, password })
    return await login(email, password)
  } catch (err) {
    authStore.setError(err.message)
    throw err
  }
}

export async function initAuth() {
  const auth = get(authStore)

  if (!auth.token || auth.token.trim() === '') {
    return false
  }

  try {
    const user = await api('GET', '/auth/me')
    authStore.setUser(user)
    return true
  } catch (err) {
    authStore.logout()
    return false
  }
}
