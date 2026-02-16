/**
 * client.js — Shared API client
 *
 * Architecture: ONE core function (coreFetch) calls fetch().
 * All safeguards live there: auth, JWT validation, requestModifier,
 * retry with exponential backoff, error handling.
 *
 * Everything hangs off `api`:
 *   api(method, path, data, options)  → auto-detect JSON/raw
 *   api.get(path)                     → GET shorthand
 *   api.post(path, data)              → POST shorthand
 *   api.put(path, data)               → PUT shorthand
 *   api.del(path)                     → DELETE shorthand
 *   api.stream(method, path, data, onMessage)  → SSE reader (no retry)
 *   api.upload(path, formData, onMessage)      → multipart SSE
 *   api.configure({ baseUrl, ... })   → update global config
 *   api.create({ baseUrl, ... })      → scoped client instance
 *
 * @example
 *   import { api } from '@myorg/ui'
 *
 *   api.configure({ baseUrl: '/api/v1' })
 *
 *   const users = await api.get('/users')
 *   const user  = await api.post('/users', { name: 'John' })
 *   const csv   = await api.get('/export/csv')  // raw Response (not JSON)
 *
 *   // Scoped client for 3rd party APIs
 *   const stripe = api.create({
 *     baseUrl: 'https://api.stripe.com',
 *     unwrap: (r) => r.data,
 *     parseError: (b) => b.error?.message,
 *   })
 *   const charges = await stripe.get('/charges')
 */
import { get } from 'svelte/store'
import { useAuth, getAuthToken } from '../hooks/auth.js'

// =============================================================================
// Configuration
// =============================================================================

/**
 * Default unwrap: extracts `data` from `{ success: true, data: ... }` responses.
 * Returns the full result for any other shape.
 *
 * @param {*} result - Parsed JSON response body
 * @returns {*} Unwrapped data or original result
 */
function defaultUnwrap(result) {
  if (result?.success === true && result.data !== undefined) {
    return result.data
  }
  return result
}

/**
 * @typedef {Object} ApiConfig
 * @property {string} baseUrl - Base URL prepended to all paths (default: '/api/v1')
 * @property {((msg: string) => void)|null} onUnauthorized - Called on 401 responses
 * @property {((url: string, path: string, headers: Object, options: Object) => {url?: string, headers?: Object, error?: string})|null} requestModifier - Rewrite URL/headers per-request. Return `{ error }` to skip the request.
 * @property {((result: *) => *)|null} unwrap - Extract payload from JSON responses. `null` to disable.
 * @property {((body: Object) => string|null)|null} parseError - Extract error message from non-standard error bodies. Falls back to `detail`/`error`/`message`.
 */

/** @type {ApiConfig} */
let config = {
  baseUrl: '/api/v1',
  onUnauthorized: null,
  requestModifier: null,
  unwrap: defaultUnwrap,
  parseError: null,
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

function extractErrorMessage(body, cfg) {
  if (cfg.parseError) {
    const custom = cfg.parseError(body)
    if (custom) return custom
  }
  return body.detail || body.error || body.message || null
}

async function handleErrorResponse(res, options = {}) {
  const cfg = options.config || config

  if (res.status === 401) {
    let errorMsg = 'Authentication failed'
    try {
      const errBody = await res.json()
      errorMsg = extractErrorMessage(errBody, cfg) || errorMsg
    } catch {
      try {
        const text = await res.text()
        if (text) errorMsg = text.slice(0, 200)
      } catch {}
    }

    const auth = get(useAuth)
    if (auth.token) {
      useAuth.logout()
      errorMsg = 'Session expired - please login again'
    }

    if (cfg.onUnauthorized) {
      cfg.onUnauthorized(errorMsg)
    }

    throw apiError(errorMsg, 401)
  }

  if (!res.ok) {
    let detail = 'Request failed'
    try {
      const err = await res.json()
      detail = extractErrorMessage(err, cfg) || detail
    } catch {
      try {
        const text = await res.text()
        if (text) detail = text.slice(0, 200)
      } catch {}
    }
    throw apiError(detail, res.status)
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
// SSE Stream Reader (single-line data: only — no multi-line/event/id support)
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
  noRetry = false,
  throwOnSkip = false,
  signal = null,
  config: cfg,
  ...options
} = {}) {
  cfg = cfg || config
  const auth = get(useAuth)

  // --- Auth + JWT validation ---
  const isAuthEndpoint = path.startsWith('/auth/login') || path.startsWith('/auth/register')

  if (auth.token && !isAuthEndpoint && !options.skipAuth) {
    if (!isValidJwtFormat(auth.token)) {
      console.error('Invalid JWT format detected - token may be corrupted')
      useAuth.logout()
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
  if (signal) fetchOpts.signal = signal
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
// Core api function + methods
// =============================================================================

/**
 * @typedef {Object} RequestOptions
 * @property {AbortSignal} [signal] - AbortController signal to cancel the request
 * @property {Object} [headers] - Additional headers to merge
 * @property {number} [retries] - Override retry count for this request
 * @property {boolean} [skipAuth] - Skip auth header and JWT validation
 * @property {ApiConfig} [config] - Per-request config override (used by scoped clients)
 */

/**
 * Make an API request. Auto-detects response type by content-type header:
 * JSON responses are parsed and run through `unwrap`. Everything else
 * (blobs, files, HTML) returns the raw `Response` object.
 *
 * @param {'GET'|'POST'|'PUT'|'DELETE'} method - HTTP method
 * @param {string} path - API path appended to baseUrl (e.g. '/users')
 * @param {Object|FormData|null} [data=null] - Request body (auto-serialized to JSON unless FormData)
 * @param {RequestOptions} [options={}] - Request options
 * @returns {Promise<*>} Parsed + unwrapped JSON, raw Response, or null (204/skipped)
 *
 * @example
 *   const users = await api('GET', '/users')
 *   const user  = await api('POST', '/users', { name: 'John' })
 *   const csv   = await api('GET', '/export/csv')  // → raw Response
 */
export async function api(method, path, data = null, options = {}) {
  const cfg = options.config || config
  const res = await coreFetch(method, path, { data, ...options })
  if (res === null) return null
  if (res.status === 204) return null

  const contentType = res.headers.get('content-type') || ''
  if (!contentType.includes('application/json')) {
    return res
  }

  const result = await res.json()

  if (cfg.unwrap) {
    return cfg.unwrap(result)
  }
  return result
}

// --- Convenience shorthands ---

/**
 * GET request.
 * @param {string} path - API path
 * @param {RequestOptions} [options]
 * @returns {Promise<*>}
 * @example const users = await api.get('/users')
 */
api.get = (path, options) => api('GET', path, null, options)

/**
 * POST request.
 * @param {string} path - API path
 * @param {Object|FormData|null} data - Request body
 * @param {RequestOptions} [options]
 * @returns {Promise<*>}
 * @example const user = await api.post('/users', { name: 'John' })
 */
api.post = (path, data, options) => api('POST', path, data, options)

/**
 * PUT request.
 * @param {string} path - API path
 * @param {Object|FormData|null} data - Request body
 * @param {RequestOptions} [options]
 * @returns {Promise<*>}
 * @example await api.put('/users/1', { name: 'Jane' })
 */
api.put = (path, data, options) => api('PUT', path, data, options)

/**
 * DELETE request.
 * @param {string} path - API path
 * @param {RequestOptions} [options]
 * @returns {Promise<*>}
 * @example await api.del('/users/1')
 */
api.del = (path, options) => api('DELETE', path, null, options)

// --- Streaming ---

/**
 * SSE streaming call. Opens a connection and calls `onMessage` for each
 * `data:` line parsed as JSON. No retry (can't retry mid-stream).
 *
 * @param {'GET'|'POST'|'PUT'|'DELETE'} method - HTTP method
 * @param {string} path - API path
 * @param {Object|null} data - Request body
 * @param {(message: Object) => void} onMessage - Called for each SSE event
 * @param {RequestOptions} [options]
 * @returns {Promise<void>} Resolves when stream ends
 *
 * @example
 *   await api.stream('POST', '/chat', { message: 'hi' }, (msg) => {
 *     console.log(msg)
 *   })
 */
api.stream = async (method, path, data, onMessage, options = {}) => {
  const res = await coreFetch(method, path, { data, noRetry: true, throwOnSkip: true, ...options })
  await readSSE(res, onMessage)
}

/**
 * Multipart SSE streaming (file upload + SSE response).
 * Sends FormData via POST and reads back an SSE stream.
 *
 * @param {string} path - API path
 * @param {FormData} formData - Multipart form data (files, fields)
 * @param {(message: Object) => void} onMessage - Called for each SSE event
 * @param {RequestOptions} [options]
 * @returns {Promise<void>} Resolves when stream ends
 *
 * @example
 *   const form = new FormData()
 *   form.append('file', file)
 *   await api.upload('/documents/process', form, (msg) => {
 *     console.log(msg.progress)
 *   })
 */
api.upload = async (path, formData, onMessage, options = {}) => {
  const res = await coreFetch('POST', path, { data: formData, noRetry: true, throwOnSkip: true, ...options })
  await readSSE(res, onMessage)
}

// --- Configuration ---

/**
 * Update global API configuration. Merges with existing config.
 *
 * @param {Partial<ApiConfig>} newConfig - Config values to merge
 *
 * @example
 *   api.configure({ baseUrl: '/api/v2' })
 *   api.configure({
 *     unwrap: null,  // disable auto-unwrap
 *     parseError: (body) => body.reason,
 *     requestModifier(url, path, headers) {
 *       headers['X-Custom'] = 'value'
 *       return { url, headers }
 *     },
 *   })
 */
api.configure = (newConfig) => {
  config = { ...config, ...newConfig }
}

/**
 * Create a scoped API client with its own configuration.
 * Inherits the current global config, overridden by `customConfig`.
 * The returned client has the same shape as `api` (get/post/put/del/stream/upload)
 * but does not have `configure` or `create` — it's a leaf client.
 *
 * @param {Partial<ApiConfig>} [customConfig={}] - Config overrides for this client
 * @returns {typeof api} Scoped API client
 *
 * @example
 *   const stripe = api.create({
 *     baseUrl: 'https://api.stripe.com',
 *     unwrap: (r) => r.data,
 *     parseError: (b) => b.error?.message,
 *   })
 *   const charges = await stripe.get('/charges')
 */
api.create = (customConfig = {}) => {
  const clientConfig = { ...config, ...customConfig }

  /** @type {typeof api} */
  const scoped = (method, path, data = null, options = {}) =>
    api(method, path, data, { ...options, config: clientConfig })

  /** @see api.get */
  scoped.get = (path, options) => scoped('GET', path, null, options)
  /** @see api.post */
  scoped.post = (path, data, options) => scoped('POST', path, data, options)
  /** @see api.put */
  scoped.put = (path, data, options) => scoped('PUT', path, data, options)
  /** @see api.del */
  scoped.del = (path, options) => scoped('DELETE', path, null, options)
  /** @see api.stream */
  scoped.stream = (method, path, data, onMessage, options = {}) =>
    api.stream(method, path, data, onMessage, { ...options, config: clientConfig })
  /** @see api.upload */
  scoped.upload = (path, formData, onMessage, options = {}) =>
    api.upload(path, formData, onMessage, { ...options, config: clientConfig })

  return scoped
}

// =============================================================================
// Auth Flows (app_kernel standard endpoints)
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
    useAuth.logout()
    return false
  }
}
