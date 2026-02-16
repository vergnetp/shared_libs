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
 * Useful on logout or when you want a clean slate.
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
// Low-level SWR engine
// =============================================================================

/**
 * Low-level SWR engine with caching, background refresh, and persistence.
 *
 * Features:
 * - Returns cached data immediately (stale) — from memory or localStorage
 * - Revalidates in background
 * - Configurable refresh intervals with jitter (±10%)
 * - Deduplication of concurrent requests
 * - AbortController: cancels in-flight requests on cleanup
 * - Circuit breaker with auto-recovery cooldown
 * - localStorage persistence (default on)
 * - onSuccess / onError callbacks
 * - Manual refresh support
 */
function createFetchStore(key, fetcher, options = {}) {
  const {
    refreshInterval = 0, // Auto-refresh interval in ms (0 = disabled)
    revalidateOnFocus = true, // Refresh when tab becomes visible
    revalidateOnMount = true, // Fetch on first subscriber
    dedupingInterval = 2000, // Dedupe requests within this window
    initialData = null,
    errorCooldown = 30000, // Resume polling after this many ms of errors
    persist = true, // Persist to localStorage
    persistTTL = 0, // Max cache age in ms (0 = no expiry)
    onSuccess = null, // (data) => void
    onError = null, // (error) => void
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

  // Internal state
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

  // Fetch with deduplication, circuit breaker, and abort support.
  // Retries with backoff are handled by the API client (withRetry);
  // this layer only tracks consecutive failures to pause polling.
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
      consecutiveErrors = 0; // Cooldown elapsed, retry
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

  // Visibility change handler
  function handleVisibility() {
    if (document.visibilityState === "visible" && revalidateOnFocus) {
      consecutiveErrors = 0;
      doFetch();
    }
  }

  // Start/stop background refresh with per-tick jitter
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

  // Custom subscribe that tracks subscribers
  const { subscribe: originalSubscribe } = store;

  function subscribe(run) {
    subscriberCount++;

    // First subscriber: setup
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

      // Last subscriber: cleanup
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
 * SWR (Stale-While-Revalidate) for an API endpoint.
 *
 * String endpoint: fetches once, caches, revalidates in background.
 *   const snapshots = SWR('/infra/snapshots', { refreshInterval: 60000 })
 *
 * Function endpoint: re-fetches when params change (with abort on param change).
 *   const containers = SWR(p => `/infra/agent/${p.server}/containers`)
 *
 * Dependent fetching: waits for dependencies before fetching.
 *   const details = SWR(
 *     ($server) => `/servers/${$server.data.id}/details`,
 *     {
 *       dependencies: [serverStore],
 *       enabled: ($server) => !!$server.data,
 *     }
 *   )
 *
 * Options:
 *   refreshInterval   - Auto-refresh in ms (0 = disabled)
 *   revalidateOnFocus - Refresh on tab focus (default: true)
 *   revalidateOnMount - Fetch on first subscriber (default: true)
 *   dedupingInterval  - Dedupe window in ms (default: 2000)
 *   persist           - Cache in localStorage (default: true)
 *   persistTTL        - Max cache age in ms (0 = no expiry)
 *   errorCooldown     - Circuit breaker cooldown in ms (default: 30000)
 *   transform         - Transform response data (default: identity)
 *   onSuccess         - Callback on successful fetch
 *   onError           - Callback on failed fetch
 *   dependencies      - Array of stores to watch
 *   enabled           - (...storeValues) => boolean, gates fetching
 *
 * @param {string|Function} endpoint - API endpoint or function returning one
 * @param {Object} options - Fetch store options
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

function _swrParam(endpointFn, options = {}) {
  const {
    transform = (d) => d,
    apiFn = api,
    persist = true,
    persistTTL = 0,
    onSuccess = null,
    onError = null,
  } = options;

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

    // Same params, return existing promise
    if (fetchPromise && JSON.stringify(params) === JSON.stringify(lastParams)) {
      return fetchPromise;
    }

    // Abort previous request on param change
    if (abortController) abortController.abort();
    abortController = new AbortController();
    const { signal } = abortController;

    lastParams = params;
    lastEndpoint = endpoint;

    // Check localStorage cache for this endpoint
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
        // Resolve endpoint (may use dep values)
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
        // Dependencies no longer met — tear down
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

  // Track subscribers to manage lifecycle
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
