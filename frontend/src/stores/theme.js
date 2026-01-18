/**
 * theme.js - Theme (dark/light mode) store
 * 
 * Usage:
 *   import { theme, toggleTheme, setTheme } from '@myorg/ui'
 *   
 *   $theme // 'dark' or 'light'
 *   toggleTheme()
 *   setTheme('light')
 */
import { writable } from 'svelte/store'

const STORAGE_KEY = 'theme'
const DEFAULT_THEME = 'dark'

function getInitialTheme() {
  if (typeof localStorage === 'undefined') return DEFAULT_THEME
  return localStorage.getItem(STORAGE_KEY) || DEFAULT_THEME
}

function applyTheme(themeName) {
  if (typeof document !== 'undefined') {
    document.documentElement.setAttribute('data-theme', themeName)
  }
}

function createThemeStore() {
  const initial = getInitialTheme()
  const { subscribe, set } = writable(initial)
  
  // Apply initial theme
  applyTheme(initial)
  
  return {
    subscribe,
    set(themeName) {
      if (typeof localStorage !== 'undefined') {
        localStorage.setItem(STORAGE_KEY, themeName)
      }
      applyTheme(themeName)
      set(themeName)
    }
  }
}

export const theme = createThemeStore()

export function setTheme(themeName) {
  theme.set(themeName)
}

export function toggleTheme() {
  theme.update(current => {
    const next = current === 'dark' ? 'light' : 'dark'
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem(STORAGE_KEY, next)
    }
    applyTheme(next)
    return next
  })
}
