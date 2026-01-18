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

function buildRequest(path, options = {}) {
  const cfg = options.config || config
  const authState = get(authStore)
  
  const headers = { 'Content-Type': 'application/json' }
  
  // Add auth header unless skipped
  const isAuthEndpoint = path.startsWith('/auth/login') || path.startsWith('/auth/register')
  
  if (authState.token && !isAuthEndpoint && !options.skipAuth) {
    headers['Authorization'] = `Bearer ${authState.token}`
  }
  
  // Add custom headers
  if (options.headers) {
    Object.assign(headers, options.headers)
  }
  
  const url = cfg.baseUrl + path
  
  return { url, headers }
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
    
    const authState = get(authStore)
    if (authState.token) {
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
// Main API Function
// =============================================================================

async function apiRequest(method, path, data = null, options = {}) {
  const { url, headers } = buildRequest(path, options)
  
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
  const { url, headers } = buildRequest(path, options)
  
  const opts = { method, headers }
  if (data) opts.body = JSON.stringify(data)
  
  const res = await fetch(url, opts)
  
  await handleErrorResponse(res, options)
  
  // Read SSE stream
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
          const msg = JSON.parse(line.slice(6))
          onMessage(msg)
        } catch (e) {
          // Ignore parse errors
        }
      }
    }
  }
}

// =============================================================================
// Export Main Functions
// =============================================================================

export async function api(method, path, data = null, options = {}) {
  return apiRequest(method, path, data, options)
}

export async function apiStream(method, path, data, onMessage, options = {}) {
  return apiStreamRequest(method, path, data, onMessage, options)
}

// Convenience methods
export const get_ = (path, options) => api('GET', path, null, options)
export const post = (path, data, options) => api('POST', path, data, options)
export const put = (path, data, options) => api('PUT', path, data, options)
export const del = (path, options) => api('DELETE', path, null, options)
