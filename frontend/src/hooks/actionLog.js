/**
 * actionLog.js — Circular buffer of last N user actions for bug replay
 *
 * Records API calls, tab navigation, button clicks, and errors.
 * Auto-saves to backend on error.
 *
 * Usage:
 *   import { actionLog } from '@myorg/ui'
 *
 *   // Configure save endpoint (call once on app init)
 *   actionLog.configure({ saveUrl: '/api/v1/action-replay' })
 *
 *   // Auto-recorded by api client (no manual calls needed for API)
 *   // Manual recording for UI actions:
 *   actionLog.record('click', { target: 'deploy-btn' })
 *   actionLog.record('navigate', { tab: 'services' })
 *
 *   // On error, auto-saves to backend (fire-and-forget)
 *   // Also: window.__replay() in console, Ctrl+Shift+R → clipboard
 */

const MAX_ENTRIES = 25
let buffer = []
let seq = 0
let lastSaveAt = 0
const SAVE_COOLDOWN_MS = 30_000

let _config = {
  saveUrl: null,          // e.g. '/api/v1/action-replay' — null disables auto-save
  extraHeaders: {},       // e.g. { 'X-DO-Token': '...' }
}

// ─── Recording ───

function record(type, detail = {}) {
  seq++
  const entry = {
    seq,
    type,
    detail,
    ts: new Date().toISOString(),
    elapsed_ms: Math.round(performance.now()),
  }
  buffer.push(entry)
  if (buffer.length > MAX_ENTRIES) buffer.shift()
}

function dump() {
  return [...buffer]
}

function dumpFormatted() {
  return buffer.map(e => {
    const d = e.detail
    let summary = e.type
    if (e.type === 'api_call') summary = `${d.method} ${d.path}`
    if (e.type === 'api_error') summary = `${d.method} ${d.path} → ${d.status || 'ERR'}: ${d.error}`
    if (e.type === 'api_response') summary = `${d.method} ${d.path} → ${d.status} (${d.duration_ms}ms)`
    if (e.type === 'navigate') summary = `→ tab:${d.tab}`
    if (e.type === 'click') summary = `click: ${d.target}`
    if (e.type === 'stream_start') summary = `stream: ${d.method} ${d.path}`
    if (e.type === 'stream_end') summary = `stream done: ${d.path} (${d.duration_ms}ms)`
    if (e.type === 'error') summary = `ERROR: ${d.message}`
    return `[${e.seq}] ${e.ts.slice(11, 23)} ${summary}`
  }).join('\n')
}

function clear() {
  buffer = []
  seq = 0
}

// ─── Configuration ───

function configure(opts = {}) {
  Object.assign(_config, opts)
  actionLog._userConfigured = true
}

// ─── Auto-save to backend ───

function saveToBackend(errorMessage, errorSource) {
  if (!_config.saveUrl) return
  const now = Date.now()
  if (now - lastSaveAt < SAVE_COOLDOWN_MS) return
  if (buffer.length < 2) return
  lastSaveAt = now

  const payload = {
    error_message: (errorMessage || '').slice(0, 500),
    error_source: errorSource || 'unknown',
    url: window.location.href,
    user_agent: navigator.userAgent,
    replay_log: JSON.stringify(dump()),
  }

  // Raw fetch — avoids circular dependency with api client
  fetch(_config.saveUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ..._config.extraHeaders },
    body: JSON.stringify(payload),
  }).catch(() => {})
}

export const actionLog = { record, dump, dumpFormatted, clear, configure, saveToBackend }

// ─── Browser integrations (console, keyboard, error capture) ───

if (typeof window !== 'undefined') {
  window.__replay = () => {
    const formatted = dumpFormatted()
    console.log('%c─── Action Replay ───', 'color: #3b82f6; font-weight: bold')
    console.log(formatted)
    return formatted
  }

  window.addEventListener('keydown', (e) => {
    if (e.ctrlKey && e.shiftKey && e.key === 'R') {
      e.preventDefault()
      const text = dumpFormatted()
      navigator.clipboard.writeText(text).then(() => {
        console.log('%c✓ Action replay copied to clipboard', 'color: #22c55e')
      }).catch(() => {
        console.log(text)
      })
    }
  })

  window.addEventListener('error', (e) => {
    record('error', {
      message: e.message,
      filename: e.filename?.split('/').pop(),
      line: e.lineno,
      col: e.colno,
    })
    saveToBackend(e.message, 'js_error')
  })

  window.addEventListener('unhandledrejection', (e) => {
    const msg = e.reason?.message || String(e.reason)
    record('error', { message: msg, type: 'unhandled_promise' })
    saveToBackend(msg, 'unhandled_promise')
  })
}
