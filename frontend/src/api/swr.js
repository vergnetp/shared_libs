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
 * - Online/offline awareness (pauses when offline, revalidates on reconnect)
 * - onSuccess / onError callbacks
 * - Dependent fetching (wait for other stores before fetching)
 *
 * @example
 *   // Basic — poll every 60s, cached in localStorage
 *   const snapshots = SWR('/infra/snapshots', { refreshInterval: 60000 })
 *
 *   // Parameterised — refetches when params change, aborts stale requests
 *   const containers = SWR(p => `/infra/agent/${p.server}/containers`, {
 *     refreshInterval: 10000,
 *   })
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
import { isOnline } from "../hooks/online.js";

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
  } catch {}
}

function cacheDelete(key) {
  try {
    localStorage.removeItem(CACHE_PREFIX + key);
  } catch {}
}

/**
 * Clear all SWR cache entries from localStorage.
 * Called automatically by `logout()`. Can also be called manually.
 *
 * @example
 *   import { clearSWRCache } from './swr.js'
 *   clearSWRCache()
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

/**
 * Read cached data for a key if valid.
 * @param {string} key
 * @param {boolean} persist
 * @param {number} persistTTL
 * @returns {*|null}
 */
function readCache(key, persist, persistTTL) {
  if (!persist) return null;
  const cached = cacheGet(key);
  if (!cached) return null;
  if (persistTTL > 0 && Date.now() - cached.ts > persistTTL) return null;
  return cached.data;
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
 * @property {boolean} [revalidateOnReconnect=true] - Refetch when browser comes back online
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
// SWR Engine — shared resilience layer
//
// Owns: store, circuit breaker, abort, dedupe, interval with jitter,
//       visibility handler, online/offline handler, subscriber lifecycle,
//       localStorage cache.
//
// Used by both createFetchStore (fixed key) and _swrParam (dynamic key).
// =============================================================================

/**
 * @param {Object} engineOptions
 * @param {SWROptions} engineOptions.options - SWR config
 * @param {*} [engineOptions.initialData=null] - Resolved initial data (after cache check)
 * @returns {Object} Engine internals for the wrapper to compose
 */
function createEngine(engineOptions) {
  const {
    options: {
      refreshInterval = 0,
      revalidateOnFocus = true,
      revalidateOnReconnect = true,
      dedupingInterval = 2000,
      errorCooldown = 30000,
      persist = true,
      onSuccess = null,
      onError = null,
    },
    initialData = null,
  } = engineOptions;

  const MAX_ERRORS_BEFORE_PAUSE = 3;

  /** @type {import('svelte/store').Writable<SWRState>} */
  const store = writable({
    data: initialData,
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

  // --- Core fetch with all guards ---

  /**
   * @param {string} key - Cache key for this fetch
   * @param {(signal: AbortSignal) => Promise<*>} fetcher
   * @param {Object} [fetchOptions]
   * @param {boolean} [fetchOptions.force=false] - Skip dedupe + circuit breaker
   * @param {boolean} [fetchOptions.resetErrors=false] - Reset circuit breaker (e.g. new params)
   * @returns {Promise<*>}
   */
  async function doFetch(key, fetcher, { force = false, resetErrors = false } = {}) {
    // Skip when offline — serve from cache, don't error
    if (!isOnline()) return Promise.resolve(get(store).data);

    if (!force && fetchPromise) return fetchPromise;

    if (resetErrors) consecutiveErrors = 0;

    const now = Date.now();
    if (!force && now - lastFetchTime < dedupingInterval) {
      return Promise.resolve(get(store).data);
    }

    // Circuit breaker
    if (consecutiveErrors >= MAX_ERRORS_BEFORE_PAUSE) {
      if (now - lastErrorTime < errorCooldown)
        return Promise.resolve(get(store).data);
      consecutiveErrors = 0;
    }

    // Abort previous in-flight request
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

  // --- Lifecycle: interval, visibility, online/offline, subscribers ---

  /** @type {(() => void)|null} - Called on visibility change, reconnect, and interval tick */
  let onPoll = null;

  function handleVisibility() {
    if (document.visibilityState === "visible" && revalidateOnFocus) {
      consecutiveErrors = 0;
      if (onPoll) onPoll();
    }
  }

  function handleOnline() {
    if (revalidateOnReconnect) {
      consecutiveErrors = 0;
      if (onPoll) onPoll();
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
        if (isOnline() && onPoll) onPoll();
        const jitter = refreshInterval * (0.9 + Math.random() * 0.2);
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

  /**
   * Wrap a store's subscribe to manage lifecycle.
   * @param {(run: Function) => (() => void)} originalSubscribe
   * @param {Object} hooks
   * @param {() => void} [hooks.onFirst] - Called when first subscriber attaches
   * @param {() => void} [hooks.onLast] - Called when last subscriber detaches (before cleanup)
   * @param {() => void} [hooks.onPoll] - Called on interval tick, visibility change, and reconnect
   * @returns {(run: Function) => (() => void)}
   */
  function wrapSubscribe(originalSubscribe, hooks = {}) {
    onPoll = hooks.onPoll || null;

    return function subscribe(run) {
      subscriberCount++;

      if (subscriberCount === 1) {
        if (typeof document !== "undefined") {
          if (revalidateOnFocus) {
            document.addEventListener("visibilitychange", handleVisibility);
          }
        }
        if (typeof window !== "undefined") {
          if (revalidateOnReconnect) {
            window.addEventListener("online", handleOnline);
          }
        }
        if (hooks.onFirst) hooks.onFirst();
        startInterval();
      }

      const unsubscribe = originalSubscribe(run);

      return () => {
        unsubscribe();
        subscriberCount--;

        if (subscriberCount === 0) {
          if (hooks.onLast) hooks.onLast();
          if (abortController) abortController.abort();
          if (typeof document !== "undefined") {
            document.removeEventListener("visibilitychange", handleVisibility);
          }
          if (typeof window !== "undefined") {
            window.removeEventListener("online", handleOnline);
          }
          stopInterval();
        }
      };
    };
  }

  return {
    store,
    doFetch,
    wrapSubscribe,
    resetErrors: () => { consecutiveErrors = 0; },
    abort: () => { if (abortController) abortController.abort(); },
    getState: () => get(store),
  };
}

// =============================================================================
// Static SWR store — fixed key/fetcher
// =============================================================================

/**
 * Create a fetch store for a fixed endpoint.
 * Use `SWR()` instead of calling this directly.
 *
 * @param {string} key - Cache key (typically the endpoint path)
 * @param {(signal: AbortSignal) => Promise<*>} fetcher
 * @param {SWROptions} [options={}]
 * @returns {SWRStore}
 */
function createFetchStore(key, fetcher, options = {}) {
  const {
    initialData = null,
    revalidateOnMount = true,
    persist = true,
    persistTTL = 0,
  } = options;

  // Resolve initial data: explicit > cache > null
  const resolvedInitial = initialData ?? readCache(key, persist, persistTTL);

  const engine = createEngine({ options, initialData: resolvedInitial });
  const { store, doFetch, wrapSubscribe, resetErrors } = engine;

  const poll = () => doFetch(key, fetcher);

  const subscribe = wrapSubscribe(store.subscribe, {
    onFirst: revalidateOnMount ? poll : undefined,
    onPoll: poll,
  });

  return {
    subscribe,
    refresh: () => {
      resetErrors();
      return doFetch(key, fetcher, { force: true });
    },
    mutate: (data) => {
      store.update((s) => ({ ...s, data, error: null }));
      if (persist) cacheSet(key, data);
    },
    invalidate: () => {
      cacheDelete(key);
      resetErrors();
      return doFetch(key, fetcher, { force: true });
    },
    get: () => get(store),
  };
}

// =============================================================================
// Parameterised SWR — dynamic key/fetcher based on params
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
  } = options;

  const engine = createEngine({ options });
  const { store, doFetch, wrapSubscribe, resetErrors, abort } = engine;

  let lastParams = null;
  let lastEndpoint = null;

  function makeFetcher(endpoint) {
    return async (signal) => {
      const data = await apiFn("GET", endpoint, null, { signal });
      return transform(data);
    };
  }

  async function fetchParams(params) {
    const endpoint = endpointFn(params);
    if (!endpoint) {
      store.set({ data: null, error: null, loading: false, lastFetched: null });
      return;
    }

    const isNewParams = JSON.stringify(params) !== JSON.stringify(lastParams);
    lastParams = params;
    lastEndpoint = endpoint;

    // Show cached data immediately for new endpoint
    if (isNewParams) {
      const cached = readCache(endpoint, persist, persistTTL);
      if (cached !== null) {
        store.update((s) => ({ ...s, data: cached }));
      }
    }

    return doFetch(endpoint, makeFetcher(endpoint), {
      force: isNewParams,
      resetErrors: isNewParams,
    });
  }

  const subscribe = wrapSubscribe(store.subscribe, {
    onPoll: () => lastParams && fetchParams(lastParams),
  });

  return {
    subscribe,
    fetch: fetchParams,
    refresh: () => {
      if (!lastParams) return;
      resetErrors();
      return doFetch(lastEndpoint, makeFetcher(lastEndpoint), { force: true });
    },
    clear: () => {
      abort();
      if (lastEndpoint && persist) cacheDelete(lastEndpoint);
      lastParams = null;
      lastEndpoint = null;
      resetErrors();
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

  // Outer engine: only manages subscriber lifecycle (no polling/visibility/reconnect
  // — the inner createFetchStore has its own engine for that).
  const engine = createEngine({
    options: {
      refreshInterval: 0,
      revalidateOnFocus: false,
      revalidateOnReconnect: false,
    },
  });
  const { store } = engine;

  let innerStore = null;
  let innerUnsub = null;
  let depUnsubs = [];

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

  function setup() {
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

  const subscribe = engine.wrapSubscribe(store.subscribe, {
    onFirst: setup,
    onLast: teardown,
  });

  return {
    subscribe,
    refresh: () => innerStore?.refresh(),
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
 * ```
 *
 * **Parameterised endpoint** — function, refetches on param change:
 * ```js
 * const containers = SWR(p => `/infra/agent/${p.server}/containers`, {
 *   refreshInterval: 10000,
 * })
 * containers.fetch({ server: 'web1' })
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