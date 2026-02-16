/**
 * swr.js — Stale-While-Revalidate data fetching for Svelte
 *
 * Returns cached data immediately (from memory or localStorage),
 * then revalidates in background. Svelte store compatible.
 *
 * Features:
 * - localStorage persistence (default on, survives page reloads)
 * - Background refresh with jittered intervals (±10%, prevents thundering herd)
 * - Deduplication of concurrent requests
 * - AbortController (cancels stale requests on param change or cleanup)
 * - Circuit breaker with auto-recovery cooldown
 * - onSuccess / onError callbacks
 * - Dependent fetching (wait for other stores before fetching)
 *
 * @example
 *   // Basic — poll every 60s, cached in localStorage
 *   const snapshots = SWR('/infra/snapshots', { refreshInterval: 60000 })
 *
 *   // Parameterised — refetches when params change, aborts stale requests
 *   const containers = SWR(p => `/infra/agent/${p.server}/containers`)
 *   containers.fetch({ server: 'web1' })
 *
 *   // Dependent — waits for server store before fetching
 *   const details = SWR(
 *     ($server) => `/servers/${$server.data.id}/details`,
 *     { dependencies: [serverStore], enabled: ($s) => !!$s.data }
 *   )
 *
 *   // In Svelte template
 *   {#if $snapshots.loading}Loading...{/if}
 *   {#if $snapshots.error}Error: {$snapshots.error}{/if}
 *   {#each $snapshots.data ?? [] as snap}...{/each}
 *
 * @module swr
 */
import { writable, get } from "svelte/store";
import { api } from "./client.js";

// =============================================================================
// Persistent Cache — localStorage with silent degradation
// =============================================================================

const CACHE_PREFIX = "swr:";

function cacheGet(key) {
  try {
    const raw = localStorage.getItem(CACHE_PREFIX + key);
    if (!raw) return null;
    const { data, ts } = JSON.parse(raw);
    return { data, ts };
  } catch {
    return null;
  }
}

function cacheSet(key, data) {
  try {
    localStorage.setItem(
      CACHE_PREFIX + key,
      JSON.stringify({ data, ts: Date.now() }),
    );
  } catch {
    // Storage full or unavailable — silently skip
  }
}

function cacheDelete(key) {
  try {
    localStorage.removeItem(CACHE_PREFIX + key);
  } catch {}
}

/**
 * Clear all SWR cache entries from localStorage.
 * Call on logout or when you want a clean slate.
 *
 * @example
 *   import { clearSWRCache } from './swr.js'
 *   function handleLogout() {
 *     useAuth.logout()
 *     clearSWRCache()
 *   }
 */
export function clearSWRCache() {
  try {
    const keys = [];
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k?.startsWith(CACHE_PREFIX)) keys.push(k);
    }
    keys.forEach((k) => localStorage.removeItem(k));
  } catch {}
}

// =============================================================================
// Types
// =============================================================================

/**
 * @typedef {Object} SWRState
 * @property {*} data - The fetched data (or null if not yet loaded)
 * @property {string|null} error - Error message from last failed fetch (null if ok)
 * @property {boolean} loading - True only on first load with no cached data
 * @property {Date|null} lastFetched - Timestamp of last successful fetch (null if never)
 */

/**
 * @typedef {Object} SWROptions
 * @property {number} [refreshInterval=0] - Auto-refresh interval in ms (0 = disabled)
 * @property {boolean} [revalidateOnFocus=true] - Refetch when browser tab becomes visible
 * @property {boolean} [revalidateOnMount=true] - Fetch on first subscriber
 * @property {number} [dedupingInterval=2000] - Dedupe requests within this window (ms)
 * @property {*} [initialData=null] - Initial data before first fetch (overrides cache)
 * @property {number} [errorCooldown=30000] - Circuit breaker: resume polling after this many ms of consecutive errors
 * @property {boolean} [persist=true] - Persist responses in localStorage
 * @property {number} [persistTTL=0] - Max cache age in ms (0 = no expiry)
 * @property {(data: *) => void} [onSuccess] - Called after each successful fetch
 * @property {(error: Error) => void} [onError] - Called after each failed fetch
 * @property {(data: *) => *} [transform] - Transform response data before storing
 * @property {typeof api} [apiFn] - API function to use (default: global `api`)
 * @property {import('svelte/store').Readable[]} [dependencies] - Stores to watch (enables dependent fetching)
 * @property {(...values: *[]) => boolean} [enabled] - Gate function for dependent fetching
 */

/**
 * @typedef {Object} SWRStore
 * @property {(run: (value: SWRState) => void) => (() => void)} subscribe - Svelte store contract
 * @property {() => Promise<*>} refresh - Force refetch, resets circuit breaker
 * @property {(data: *) => void} mutate - Optimistic update: set data + clear error + update cache
 * @property {() => Promise<*>} invalidate - Clear cache + force refetch
 * @property {() => SWRState} get - Read current state synchronously
 */

/**
 * @typedef {Object} SWRParamStore
 * @property {(run: (value: SWRState) => void) => (() => void)} subscribe - Svelte store contract
 * @property {(params: Object) => Promise<*>} fetch - Fetch with new params (aborts previous)
 * @property {() => Promise<*>|void} refresh - Refetch with last params
 * @property {() => void} clear - Abort + clear cache + reset state
 */

/**
 * @typedef {Object} SWRDependentStore
 * @property {(run: (value: SWRState) => void) => (() => void)} subscribe - Svelte store contract
 * @property {() => Promise<*>|undefined} refresh - Refresh inner store (if active)
 * @property {() => SWRState} get - Read current state synchronously
 */

// =============================================================================
// Low-level SWR engine
// =============================================================================

/**
 * Low-level fetch store with caching, background refresh, and persistence.
 * Use `SWR()` instead of calling this directly.
 *
 * @param {string} key - Cache key (typically the endpoint path)
 * @param {(signal: AbortSignal) => Promise<*>} fetcher - Async function that fetches data
 * @param {SWROptions} [options={}]
 * @returns {SWRStore}
 */
function createFetchStore(key, fetcher, options = {}) {
  const {
    refreshInterval = 0,
    revalidateOnFocus = true,
    revalidateOnMount = true,
    dedupingInterval = 2000,
    initialData = null,
    errorCooldown = 30000,
    persist = true,
    persistTTL = 0,
    onSuccess = null,
    onError = null,
  } = options;

  // Resolve initial data: explicit initialData > localStorage cache > null
  let resolvedInitial = initialData;
  if (resolvedInitial === null && persist) {
    const cached = cacheGet(key);
    if (cached) {
      const expired = persistTTL > 0 && Date.now() - cached.ts > persistTTL;
      if (!expired) resolvedInitial = cached.data;
    }
  }

  /** @type {import('svelte/store').Writable<SWRState>} */
  const store = writable({
    data: resolvedInitial,
    error: null,
    loading: false,
    lastFetched: null,
  });

  let lastFetchTime = 0;
  let fetchPromise = null;
  let intervalId = null;
  let subscriberCount = 0;
  let consecutiveErrors = 0;
  let lastErrorTime = 0;
  let abortController = null;
  const MAX_ERRORS_BEFORE_PAUSE = 3;

  async function doFetch(force = false) {
    if (!force && fetchPromise) return fetchPromise;

    const now = Date.now();
    if (!force && now - lastFetchTime < dedupingInterval) {
      return Promise.resolve(get(store).data);
    }

    // Circuit breaker: pause after repeated failures, auto-recover after cooldown
    if (consecutiveErrors >= MAX_ERRORS_BEFORE_PAUSE) {
      if (now - lastErrorTime < errorCooldown)
        return Promise.resolve(get(store).data);
      consecutiveErrors = 0;
    }

    // Abort any in-flight request
    if (abortController) abortController.abort();
    abortController = new AbortController();
    const { signal } = abortController;

    lastFetchTime = now;
    store.update((s) => ({
      ...s,
      loading: s.lastFetched === null && s.data === null,
    }));

    fetchPromise = (async () => {
      try {
        const data = await fetcher(signal);
        if (signal.aborted) return get(store).data;
        consecutiveErrors = 0;
        store.set({
          data,
          error: null,
          loading: false,
          lastFetched: new Date(),
        });
        if (persist) cacheSet(key, data);
        if (onSuccess) onSuccess(data);
        return data;
      } catch (error) {
        if (signal.aborted || error.name === "AbortError") {
          return get(store).data;
        }
        consecutiveErrors++;
        lastErrorTime = Date.now();
        const msg = error.message || "Fetch failed";
        store.update((s) => ({
          ...s,
          error: msg,
          loading: false,
        }));
        if (onError) onError(error);
        throw error;
      } finally {
        fetchPromise = null;
      }
    })();

    return fetchPromise;
  }

  function handleVisibility() {
    if (document.visibilityState === "visible" && revalidateOnFocus) {
      consecutiveErrors = 0;
      doFetch();
    }
  }

  function startInterval() {
    if (refreshInterval > 0 && !intervalId) {
      const tick = () => {
        if (
          typeof document !== "undefined" &&
          document.visibilityState !== "visible"
        ) {
          intervalId = setTimeout(tick, refreshInterval);
          return;
        }
        doFetch();
        const jitter = refreshInterval * (0.9 + Math.random() * 0.2); // ±10%
        intervalId = setTimeout(tick, jitter);
      };
      intervalId = setTimeout(tick, refreshInterval);
    }
  }

  function stopInterval() {
    if (intervalId) {
      clearTimeout(intervalId);
      intervalId = null;
    }
  }

  const { subscribe: originalSubscribe } = store;

  function subscribe(run) {
    subscriberCount++;

    if (subscriberCount === 1) {
      if (revalidateOnFocus && typeof document !== "undefined") {
        document.addEventListener("visibilitychange", handleVisibility);
      }
      if (revalidateOnMount) {
        doFetch();
      }
      startInterval();
    }

    const unsubscribe = originalSubscribe(run);

    return () => {
      unsubscribe();
      subscriberCount--;

      if (subscriberCount === 0) {
        if (abortController) abortController.abort();
        if (typeof document !== "undefined") {
          document.removeEventListener("visibilitychange", handleVisibility);
        }
        stopInterval();
      }
    };
  }

  return {
    subscribe,
    refresh: () => {
      consecutiveErrors = 0;
      return doFetch(true);
    },
    mutate: (data) => {
      store.update((s) => ({ ...s, data, error: null }));
      if (persist) cacheSet(key, data);
    },
    invalidate: () => {
      cacheDelete(key);
      consecutiveErrors = 0;
      return doFetch(true);
    },
    get: () => get(store),
  };
}

// =============================================================================
// Public API
// =============================================================================

/**
 * Create an SWR (Stale-While-Revalidate) store for an API endpoint.
 *
 * Three modes depending on arguments:
 *
 * **Static endpoint** — string path, returns a polling store:
 * ```js
 * const snapshots = SWR('/infra/snapshots', { refreshInterval: 60000 })
 * // Use in template: {#if $snapshots.loading}...{/if}
 * ```
 *
 * **Parameterised endpoint** — function, refetches on param change:
 * ```js
 * const containers = SWR(p => `/infra/agent/${p.server}/containers`)
 * containers.fetch({ server: 'web1' })  // triggers fetch
 * containers.fetch({ server: 'web2' })  // aborts previous, fetches new
 * ```
 *
 * **Dependent endpoint** — waits for dependency stores:
 * ```js
 * const details = SWR(
 *   ($server) => `/servers/${$server.data.id}/details`,
 *   {
 *     dependencies: [serverStore],
 *     enabled: ($s) => !!$s.data,
 *   }
 * )
 * ```
 *
 * @param {string|Function} endpoint - API path or function returning one
 * @param {SWROptions} [options={}]
 * @returns {SWRStore|SWRParamStore|SWRDependentStore}
 */
export function SWR(endpoint, options = {}) {
  if (options.dependencies) {
    return _swrDependent(endpoint, options);
  }

  if (typeof endpoint === "function") {
    return _swrParam(endpoint, options);
  }

  const { transform = (d) => d, apiFn = api, ...storeOptions } = options;

  return createFetchStore(
    endpoint,
    async (signal) => {
      const data = await apiFn("GET", endpoint, null, { signal });
      return transform(data);
    },
    storeOptions,
  );
}

// =============================================================================
// Parameterised SWR — endpoint changes with params, aborts stale requests
// =============================================================================

/**
 * @param {(params: Object) => string|null} endpointFn
 * @param {SWROptions} options
 * @returns {SWRParamStore}
 */
function _swrParam(endpointFn, options = {}) {
  const {
    transform = (d) => d,
    apiFn = api,
    persist = true,
    persistTTL = 0,
    onSuccess = null,
    onError = null,
  } = options;

  /** @type {import('svelte/store').Writable<SWRState>} */
  const store = writable({
    data: null,
    error: null,
    loading: false,
    lastFetched: null,
  });

  let lastParams = null;
  let lastEndpoint = null;
  let fetchPromise = null;
  let abortController = null;

  async function fetch(params) {
    const endpoint = endpointFn(params);
    if (!endpoint) {
      store.set({ data: null, error: null, loading: false, lastFetched: null });
      return;
    }

    if (fetchPromise && JSON.stringify(params) === JSON.stringify(lastParams)) {
      return fetchPromise;
    }

    if (abortController) abortController.abort();
    abortController = new AbortController();
    const { signal } = abortController;

    lastParams = params;
    lastEndpoint = endpoint;

    // Check localStorage cache
    let cachedData = null;
    if (persist) {
      const cached = cacheGet(endpoint);
      if (cached) {
        const expired = persistTTL > 0 && Date.now() - cached.ts > persistTTL;
        if (!expired) cachedData = cached.data;
      }
    }

    store.update((s) => ({
      ...s,
      data: cachedData ?? s.data,
      loading: cachedData === null,
    }));

    fetchPromise = (async () => {
      try {
        const data = await apiFn("GET", endpoint, null, { signal });
        if (signal.aborted) return get(store).data;
        const transformed = transform(data);
        store.set({
          data: transformed,
          error: null,
          loading: false,
          lastFetched: new Date(),
        });
        if (persist) cacheSet(endpoint, transformed);
        if (onSuccess) onSuccess(transformed);
        return transformed;
      } catch (error) {
        if (signal.aborted || error.name === "AbortError") {
          return get(store).data;
        }
        const msg = error.message || "Fetch failed";
        store.update((s) => ({
          ...s,
          error: msg,
          loading: false,
        }));
        if (onError) onError(error);
        throw error;
      } finally {
        fetchPromise = null;
      }
    })();

    return fetchPromise;
  }

  return {
    subscribe: store.subscribe,
    fetch,
    refresh: () => lastParams && fetch(lastParams),
    clear: () => {
      if (abortController) abortController.abort();
      if (lastEndpoint && persist) cacheDelete(lastEndpoint);
      lastParams = null;
      lastEndpoint = null;
      store.set({ data: null, error: null, loading: false, lastFetched: null });
    },
  };
}

// =============================================================================
// Dependent SWR — waits for dependency stores before fetching
// =============================================================================

/**
 * @param {string|Function} endpoint
 * @param {SWROptions & { dependencies: import('svelte/store').Readable[], enabled?: (...values: *[]) => boolean }} options
 * @returns {SWRDependentStore}
 */
function _swrDependent(endpoint, options = {}) {
  const {
    dependencies = [],
    enabled = () => true,
    transform = (d) => d,
    apiFn = api,
    persist = true,
    persistTTL = 0,
    onSuccess = null,
    onError = null,
    ...storeOptions
  } = options;

  /** @type {import('svelte/store').Writable<SWRState>} */
  const store = writable({
    data: null,
    error: null,
    loading: false,
    lastFetched: null,
  });

  let innerStore = null;
  let innerUnsub = null;
  let depUnsubs = [];

  function setup() {
    function check() {
      const values = dependencies.map((d) => get(d));
      const ready = enabled(...values);

      if (ready && !innerStore) {
        const resolvedEndpoint =
          typeof endpoint === "function" ? endpoint(...values) : endpoint;
        if (!resolvedEndpoint) return;

        innerStore = createFetchStore(
          resolvedEndpoint,
          async (signal) => {
            const data = await apiFn("GET", resolvedEndpoint, null, { signal });
            return transform(data);
          },
          { persist, persistTTL, onSuccess, onError, ...storeOptions },
        );

        innerUnsub = innerStore.subscribe((value) => store.set(value));
      } else if (!ready && innerStore) {
        if (innerUnsub) innerUnsub();
        innerStore = null;
        innerUnsub = null;
        store.set({
          data: null,
          error: null,
          loading: false,
          lastFetched: null,
        });
      }
    }

    depUnsubs = dependencies.map((dep) => dep.subscribe(() => check()));
    check();
  }

  function teardown() {
    depUnsubs.forEach((u) => u());
    depUnsubs = [];
    if (innerUnsub) innerUnsub();
    innerStore = null;
    innerUnsub = null;
  }

  let subscriberCount = 0;
  const { subscribe: originalSubscribe } = store;

  function subscribe(run) {
    subscriberCount++;
    if (subscriberCount === 1) setup();

    const unsub = originalSubscribe(run);
    return () => {
      unsub();
      subscriberCount--;
      if (subscriberCount === 0) teardown();
    };
  }

  return {
    subscribe,
    refresh: () => innerStore?.refresh(),
    get: () => get(store),
  };
}
