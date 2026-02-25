/**
 * @myorg/ui - Shared Svelte UI Components
 * 
 * Usage:
 *   import { Auth, Header, Button } from '@myorg/ui'
 *   import { useAuth, toasts } from '@myorg/ui'
 *   import { api, login, logout, initApp } from '@myorg/ui'
 *   import { SWR, clearSWRCache } from '@myorg/ui'
 *   import { useOnlineStatus, isOnline } from '@myorg/ui'
 *   import { presets, withPreset } from '@myorg/ui/presets'
 *   import '@myorg/ui/styles/base.css'
 */

// =============================================================================
// Components
// =============================================================================
export { default as Auth } from './components/Auth.svelte'
export { default as Header } from './components/Header.svelte'
export { default as Button } from './components/Button.svelte'
export { default as Badge } from './components/Badge.svelte'
export { default as Card } from './components/Card.svelte'
export { default as Modal } from './components/Modal.svelte'
export { default as Tabs } from './components/Tabs.svelte'
export { default as ToastContainer } from './components/ToastContainer.svelte'
export { default as ThemeToggle } from './components/ThemeToggle.svelte'
export { default as OfflineBanner } from './components/OfflineBanner.svelte'

// =============================================================================
// Hooks (reactive state)
// =============================================================================
export {
  useAuth,
  isAuthenticated,
  currentUser,
  isAdmin,
  getAuthToken,
  setAuthToken,
  clearAuth,
  setAdminEmails,
  getCustomToken,
  setCustomToken,
  clearCustomToken,
} from './hooks/auth.js'

export {
  toasts,
  addToast,
  removeToast,
} from './hooks/toast.js'

export {
  theme,
  setTheme,
  toggleTheme,
} from './hooks/theme.js'

export {
  useOnlineStatus,
  isOnline,
} from './hooks/online.js'

export { actionLog } from './hooks/actionLog.js'

// =============================================================================
// API Client
// =============================================================================
export { api } from './api/client.js'

// =============================================================================
// SWR (Stale-While-Revalidate)
// =============================================================================
export { SWR, clearSWRCache } from './api/swr.js'

// =============================================================================
// App + Auth Flows
// =============================================================================
export {
  initApp,
  login,
  register,
  logout,
  initAuth,
} from './app.js'

// =============================================================================
// Presets (re-export for convenience)
// =============================================================================
export { presets, withPreset } from './presets/index.js'