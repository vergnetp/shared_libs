/**
 * auth.js - Shared authentication store
 *
 * Usage:
 *   import { authStore, isAuthenticated, setAuthToken, clearAuth } from '@myorg/ui'
 */
import { writable, derived } from "svelte/store";

// =============================================================================
// Cookie Helpers
// =============================================================================

function setCookie(name, value, days = 7) {
  if (typeof document === "undefined") return;
  const expires = new Date(Date.now() + days * 864e5).toUTCString();
  document.cookie = `${name}=${encodeURIComponent(value)}; expires=${expires}; path=/; SameSite=Strict`;
}

function getCookie(name) {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(new RegExp("(^| )" + name + "=([^;]+)"));
  return match ? decodeURIComponent(match[2]) : null;
}

function deleteCookie(name) {
  if (typeof document === "undefined") return;
  document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/; SameSite=Strict`;
}

// =============================================================================
// Auth Store
// =============================================================================

const TOKEN_COOKIE = "jwt_token";

function createAuthStore() {
  const storedToken = getCookie(TOKEN_COOKIE);

  const { subscribe, set, update } = writable({
    token: storedToken,
    user: null,
    loading: false,
    error: null,
  });

  return {
    subscribe,

    setToken(token) {
      setCookie(TOKEN_COOKIE, token, 7);
      update((s) => ({ ...s, token, error: null }));
    },

    setUser(user) {
      update((s) => ({ ...s, user }));
    },

    setError(error) {
      update((s) => ({ ...s, error, loading: false }));
    },

    setLoading(loading) {
      update((s) => ({ ...s, loading }));
    },

    logout() {
      deleteCookie(TOKEN_COOKIE);
      set({ token: null, user: null, loading: false, error: null });
    },

    clearError() {
      update((s) => ({ ...s, error: null }));
    },
  };
}

export const authStore = createAuthStore();

// =============================================================================
// Derived Stores
// =============================================================================

export const isAuthenticated = derived(
  authStore,
  ($auth) => !!$auth.token && !!$auth.user,
);

export const currentUser = derived(authStore, ($auth) => $auth.user);

// Admin check - can be customized per app via adminEmails config
let adminEmails = [];

export function setAdminEmails(emails) {
  adminEmails = emails.map((e) => e.toLowerCase());
}

export const isAdmin = derived(authStore, ($auth) => {
  if (!$auth.user?.email) return false;
  // Check role first, then email list
  if ($auth.user.role === "admin") return true;
  return adminEmails.includes($auth.user.email.toLowerCase());
});

// =============================================================================
// Helper Functions
// =============================================================================

export function getAuthToken() {
  return getCookie(TOKEN_COOKIE);
}

export function setAuthToken(token) {
  authStore.setToken(token);
}

export function clearAuth() {
  authStore.logout();
}

// =============================================================================
// Custom Token Storage (for app-specific tokens like DO token)
// =============================================================================

export function getCustomToken(name) {
  const value = getCookie(name);
  if (!value || value === "null" || value === "undefined") return null;
  return value;
}

export function setCustomToken(name, token, days = 30) {
  if (!token || token === "null" || token === "undefined") {
    deleteCookie(name);
    return;
  }
  setCookie(name, token, days);
}

export function clearCustomToken(name) {
  deleteCookie(name);
}
