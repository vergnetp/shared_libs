/**
 * useOnlineStatus — Reactive online/offline detection for Svelte
 *
 * Writable store tracking `navigator.onLine` with automatic event listeners.
 * Listeners are set up once on import (browser-only, safe for SSR).
 *
 * @example
 *   import { useOnlineStatus } from '@myorg/ui'
 *
 *   // Reactive in templates
 *   {#if !$useOnlineStatus}
 *     <OfflineBanner />
 *   {/if}
 *
 *   // Programmatic check
 *   import { isOnline } from '@myorg/ui'
 *   if (isOnline()) { ... }
 *
 *   // Subscribe to changes
 *   useOnlineStatus.subscribe(online => {
 *     if (online) console.log('Back online')
 *   })
 *
 * @module online
 */
import { writable, get } from 'svelte/store'

/**
 * Svelte store — `true` when browser is online, `false` when offline.
 * Updates automatically via `online`/`offline` window events.
 *
 * @type {import('svelte/store').Writable<boolean>}
 */
export const useOnlineStatus = writable(
  typeof navigator !== 'undefined' ? navigator.onLine : true
)

// Auto-register listeners (browser only, runs once on module load)
if (typeof window !== 'undefined') {
  window.addEventListener('online', () => useOnlineStatus.set(true))
  window.addEventListener('offline', () => useOnlineStatus.set(false))
}

/**
 * Synchronous check — read current online status without subscribing.
 *
 * @returns {boolean} `true` if browser is online
 *
 * @example
 *   if (isOnline()) {
 *     await api.post('/data', payload)
 *   }
 */
export function isOnline() {
  return get(useOnlineStatus)
}