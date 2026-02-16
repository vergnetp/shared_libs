import { writable, get } from "svelte/store";
import { api } from "./client.js";

/**
 * Low-level SWR engine with caching and background refresh.
 *
 * Features:
 * - Returns cached data immediately (stale)
 * - Revalidates in background
 * - Configurable refresh intervals
 * - Deduplication of concurrent requests
 * - Circuit breaker with auto-recovery cooldown
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
  } = options;

  // Internal state
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
  const MAX_ERRORS_BEFORE_PAUSE = 3;

  // Fetch with deduplication and circuit breaker.
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
      if (now - lastErrorTime < errorCooldown) return Promise.resolve(get(store).data);
      consecutiveErrors = 0; // Cooldown elapsed, retry
    }

    lastFetchTime = now;
    store.update((s) => ({
      ...s,
      loading: s.lastFetched === null,
    }));

    fetchPromise = (async () => {
      try {
        const data = await fetcher();
        consecutiveErrors = 0;
        store.set({
          data,
          error: null,
          loading: false,
          lastFetched: new Date(),
        });
        return data;
      } catch (error) {
        consecutiveErrors++;
        lastErrorTime = Date.now();
        store.update((s) => ({
          ...s,
          error: error.message || "Fetch failed",
          loading: false,
        }));
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

  // Start/stop background refresh
  function startInterval() {
    if (refreshInterval > 0 && !intervalId) {
      intervalId = setInterval(() => {
        if (typeof document !== "undefined" && document.visibilityState !== "visible") return;
        doFetch();
      }, refreshInterval);
    }
  }

  function stopInterval() {
    if (intervalId) {
      clearInterval(intervalId);
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
    mutate: (data) => store.update((s) => ({ ...s, data, error: null })),
    get: () => get(store),
  };
}

/**
 * SWR (Stale-While-Revalidate) for an API endpoint.
 *
 * String endpoint: fetches once, caches, revalidates in background.
 *   const snapshots = SWR('/infra/snapshots', { refreshInterval: 60000 })
 *
 * Function endpoint: re-fetches when params change.
 *   const containers = SWR(p => `/infra/agent/${p.server}/containers`)
 *
 * @param {string|Function} endpoint - API endpoint or function returning one
 * @param {Object} options - Fetch store options
 */
export function SWR(endpoint, options = {}) {
  if (typeof endpoint === "function") {
    return _swrParam(endpoint, options);
  }

  const { transform = (d) => d, apiFn = api, ...storeOptions } = options;

  return createFetchStore(
    endpoint,
    async () => {
      const data = await apiFn("GET", endpoint);
      return transform(data);
    },
    storeOptions,
  );
}

function _swrParam(endpointFn, options = {}) {
  const { transform = (d) => d, apiFn = api, ...storeOptions } = options;

  const store = writable({
    data: null,
    error: null,
    loading: false,
    lastFetched: null,
  });

  let lastParams = null;
  let fetchPromise = null;

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

    lastParams = params;
    store.update((s) => ({ ...s, loading: true }));

    fetchPromise = (async () => {
      try {
        const data = await apiFn("GET", endpoint);
        const transformed = transform(data);
        store.set({
          data: transformed,
          error: null,
          loading: false,
          lastFetched: new Date(),
        });
        return transformed;
      } catch (error) {
        store.update((s) => ({
          ...s,
          error: error.message || "Fetch failed",
          loading: false,
        }));
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
      lastParams = null;
      store.set({ data: null, error: null, loading: false, lastFetched: null });
    },
  };
}
