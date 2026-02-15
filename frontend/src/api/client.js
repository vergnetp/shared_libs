/**
 * client.js - Shared API client
 * 
 * Usage:
 *   import { api, apiStream, setApiConfig } from '@myorg/ui'
 *   
 *   // Configure once at app startup
 *   setApiConfig({ baseUrl: '/api/v1' })
 *   
 *   // Make requests
 *   const data = await api('GET', '/users')
 *   const data = await api('POST', '/users', { name: 'John' })
 *   
 *   // Auth flows (tied to app_kernel endpoints)
 *   const user = await login('user@example.com', 'pass')
 *   const user = await register('user@example.com', 'pass')
 *   const ok = await initAuth()
 */
import { get } from 'svelte/store'
import { authStore, getAuthToken } from '../stores/auth.js'

// =============================================================================
// Configuration
// =============================================================================

let config = {
  baseUrl: '/api/v1',
  onUnauthorized: null,  // Callback when 401 received
}

export function setApiConfig(newConfig) {
  config = { ...config, ...newConfig }
}

export function createApiClient(customConfig = {}) {
  const clientConfig = { ...config, ...customConfig }
  
  return {
    get: (path, options) => apiRequest('GET', path, null, { ...options, config: clientConfig }),
    post: (path, data, options) => apiRequest('POST', path, data, { ...options, config: clientConfig }),
    put: (path, data, options) => apiRequest('PUT', path, data, { ...options, config: clientConfig }),
    delete: (path, options) => apiRequest('DELETE', path, null, { ...options, config: clientConfig }),
    stream: (method, path, data, onMessage, options) => 
      apiStreamRequest(method, path, data, onMessage, { ...options, config: clientConfig }),
  }
}

// =============================================================================
// Request Builder
// =============================================================================

/**
 * Validate JWT format (must have 3 dot-separated parts)
 * @private
 */
function isValidJwtFormat(token) {
  if (!token || typeof token !== 'string') return false
  return token.split('.').length === 3
}

function buildRequest(path, options = {}) {
  const cfg = options.config || config
  const auth = get(authStore)
  
  const headers = { 'Content-Type': 'application/json' }
  
  // Add auth header unless skipped
  const isAuthEndpoint = path.startsWith('/auth/login') || path.startsWith('/auth/register')
  
  if (auth.token && !isAuthEndpoint && !options.skipAuth) {
    // Validate token format before sending
    if (!isValidJwtFormat(auth.token)) {
      console.error('Invalid JWT format detected - token may be corrupted')
      authStore.logout()
      return { error: 'Session corrupted - please login again', url: null, headers: null }
    }
    headers['Authorization'] = `Bearer ${auth.token}`
  }
  
  // Add custom headers
  if (options.headers) {
    Object.assign(headers, options.headers)
  }
  
  const url = cfg.baseUrl + path
  
  return { url, headers, error: null }
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
    
    throw new Error(errorMsg)
  }
  
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Request failed' }))
    throw new Error(err.detail || err.error || err.message || 'Request failed')
  }
}

// =============================================================================
// SSE Stream Reader (shared by apiStream and apiStreamMultipart)
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
// Main API Function
// =============================================================================

async function apiRequest(method, path, data = null, options = {}) {
  const { url, headers, error } = buildRequest(path, options)
  
  if (error) {
    console.debug(`Skipping ${path} - ${error}`)
    return null
  }
  
  const opts = { method, headers }
  if (data) opts.body = JSON.stringify(data)
  
  const res = await fetch(url, opts)
  
  await handleErrorResponse(res, options)
  
  if (res.status === 204) return null
  
  const result = await res.json()
  
  // Unwrap common response formats
  if (result && result.success === true && result.data !== undefined) {
    return result.data
  }
  
  return result
}

// =============================================================================
// Streaming API (SSE)
// =============================================================================

async function apiStreamRequest(method, path, data, onMessage, options = {}) {
  const { url, headers, error } = buildRequest(path, options)
  
  if (error) throw new Error(error)
  
  const opts = { method, headers }
  if (data) opts.body = JSON.stringify(data)
  
  const res = await fetch(url, opts)
  await handleErrorResponse(res, options)
  await readSSE(res, onMessage)
}

// =============================================================================
// Streaming Multipart (SSE + file upload)
// =============================================================================

async function apiStreamMultipartRequest(path, formData, onMessage, options = {}) {
  const cfg = options.config || config
  const auth = get(authStore)

  // Validate token format before sending
  if (auth.token && !isValidJwtFormat(auth.token)) {
    console.error('Invalid JWT format detected - token may be corrupted')
    authStore.logout()
    throw new Error('Session corrupted - please login again')
  }

  const url = cfg.baseUrl + path

  // Headers — NO Content-Type (browser sets multipart boundary)
  const headers = {}
  if (options.headers) Object.assign(headers, options.headers)
  if (auth.token) headers['Authorization'] = `Bearer ${auth.token}`

  const res = await fetch(url, { method: 'POST', headers, body: formData })
  await handleErrorResponse(res, options)
  await readSSE(res, onMessage)
}

// =============================================================================
// Raw API (returns raw Response — for blob downloads, file uploads, etc.)
// =============================================================================

async function apiRawRequest(method, path, data = null, options = {}) {
  const cfg = options.config || config
  const auth = get(authStore)

  // Validate token format before sending
  if (auth.token && !options.skipAuth && !isValidJwtFormat(auth.token)) {
    console.error('Invalid JWT format detected - token may be corrupted')
    authStore.logout()
    throw new Error('Session corrupted - please login again')
  }

  const url = cfg.baseUrl + path

  const headers = {}
  if (options.headers) Object.assign(headers, options.headers)
  if (auth.token && !options.skipAuth) {
    headers['Authorization'] = `Bearer ${auth.token}`
  }

  // Only set Content-Type for JSON data (not FormData)
  if (data && !(data instanceof FormData)) {
    headers['Content-Type'] = 'application/json'
  }

  const opts = { method, headers }
  if (data) {
    opts.body = data instanceof FormData ? data : JSON.stringify(data)
  }

  const res = await fetch(url, opts)
  await handleErrorResponse(res, options)
  return res
}

// =============================================================================
// Export Main Functions
// =============================================================================

export async function api(method, path, data = null, options = {}) {
  return apiRequest(method, path, data, options)
}

export async function apiRaw(method, path, data = null, options = {}) {
  return apiRawRequest(method, path, data, options)
}

export async function apiStream(method, path, data, onMessage, options = {}) {
  return apiStreamRequest(method, path, data, onMessage, options)
}

export async function apiStreamMultipart(path, formData, onMessage, options = {}) {
  return apiStreamMultipartRequest(path, formData, onMessage, options)
}

// Convenience methods
export const get_ = (path, options) => api('GET', path, null, options)
export const post = (path, data, options) => api('POST', path, data, options)
export const put = (path, data, options) => api('PUT', path, data, options)
export const del = (path, options) => api('DELETE', path, null, options)

// =============================================================================
// Auth Flows (app_kernel standard endpoints)
// =============================================================================

/**
 * Login user via /auth/login → /auth/me
 */
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

/**
 * Register + auto-login via /auth/register
 */
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

/**
 * Check existing token, load user if valid
 */
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
