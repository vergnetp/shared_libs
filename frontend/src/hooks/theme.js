/**
 * theme.js - Theme (dark/light/fancy) store
 * 
 * Usage:
 *   import { theme, toggleTheme, setTheme } from '@myorg/ui'
 *   
 *   $theme // 'dark', 'light', or 'fancy'
 *   toggleTheme()
 *   setTheme('fancy')
 */
import { writable, get } from 'svelte/store'

const STORAGE_KEY = 'theme'
const DEFAULT_THEME = 'dark'
const THEMES = ['dark', 'light', 'fancy']

function getInitialTheme() {
  if (typeof localStorage === 'undefined') return DEFAULT_THEME
  const stored = localStorage.getItem(STORAGE_KEY)
  return THEMES.includes(stored) ? stored : DEFAULT_THEME
}

function applyTheme(themeName) {
  if (typeof document !== 'undefined') {
    document.documentElement.setAttribute('data-theme', themeName)
  }
}

function createThemeStore() {
  const initial = getInitialTheme()
  const store = writable(initial)
  
  applyTheme(initial)
  
  return {
    subscribe: store.subscribe,
    set(themeName) {
      if (typeof localStorage !== 'undefined') {
        localStorage.setItem(STORAGE_KEY, themeName)
      }
      applyTheme(themeName)
      store.set(themeName)
    },
    update(fn) {
      const current = get(store)
      const next = fn(current)
      this.set(next)
    }
  }
}

export const theme = createThemeStore()

export function setTheme(themeName) {
  theme.set(themeName)
}

export function toggleTheme() {
  const current = get(theme)
  const idx = THEMES.indexOf(current)
  const next = THEMES[(idx + 1) % THEMES.length]
  theme.set(next)
}