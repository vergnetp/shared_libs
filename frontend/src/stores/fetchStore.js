import { writable, get } from "svelte/store";
import { api } from "../api/client.js";

/**
 * Creates an SWR-like store with caching and background refresh.
 *
 * Features:
 * - Returns cached data immediately (stale)
 * - Revalidates in background
 * - Configurable refresh intervals
 * - Deduplication of concurrent requests
 * - Manual refresh support
 *
 * @param {string} key - Unique cache key
 * @param {Function} fetcher - Async function that returns data
 * @param {Object} options - Configuration options
 * @returns {Object} Store with subscribe, refresh, and state
 */
export function createFetchStore(key, fetcher, options = {}) {
  const {
    refreshInterval = 0, // Auto-refresh interval in ms (0 = disabled)
    revalidateOnFocus = true, // Refresh when tab becomes visible
    revalidateOnMount = true, // Fetch on first subscriber
    dedupingInterval = 2000, // Dedupe requests within this window
    initialData = null,
    retryCount = 3, // Max retries on failure (0 = no retry)
    retryBaseDelay = 1000, // Base delay in ms (doubles each retry: 1s, 2s, 4s)
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
  const MAX_ERRORS_BEFORE_PAUSE = 3;

  // Fetch with deduplication and backoff
  async function doFetch(force = false) {
    // Already fetching (possibly mid-retry) — return existing promise
    if (!force && fetchPromise) {
      return fetchPromise;
    }

    const now = Date.now();

    // Dedupe: don't re-fetch if last fetch completed/started recently
    if (!force && now - lastFetchTime < dedupingInterval) {
      return;
    }

    // Back off after repeated failures (manual refresh / tab focus resets)
    if (!force && consecutiveErrors >= MAX_ERRORS_BEFORE_PAUSE) {
      return;
    }

    lastFetchTime = now;
    // Only show loading if no data exists yet (stale-while-revalidate)
    store.update((s) => ({
      ...s,
      loading: s.data === null || s.data?.length === 0,
    }));

    fetchPromise = (async () => {
      try {
        for (let attempt = 0; attempt <= retryCount; attempt++) {
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
            // Don't retry: client errors (4xx), non-retryable, last attempt, or circuit breaker tripped
            const isRetryable =
              !error.status ||
              error.status >= 500 ||
              error.name === "TypeError";

            if (
              !isRetryable ||
              attempt >= retryCount ||
              consecutiveErrors >= MAX_ERRORS_BEFORE_PAUSE
            ) {
              store.update((s) => ({
                ...s,
                error: error.message || "Fetch failed",
                loading: false,
              }));
              throw error;
            }
            // Wait with exponential backoff before next retry
            await new Promise((r) =>
              setTimeout(r, retryBaseDelay * 2 ** attempt),
            );
          }
        }
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
      intervalId = setInterval(() => doFetch(), refreshInterval);
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
    refresh: () => doFetch(true),
    mutate: (data) => store.update((s) => ({ ...s, data })),
    get: () => get(store),
  };
}

/**
 * Creates a fetch store for an API endpoint.
 *
 * @param {string} endpoint - API endpoint (e.g., '/infra/servers')
 * @param {Object} options - Fetch store options
 */
export function createApiStore(endpoint, options = {}) {
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

/**
 * Creates a parameterized fetch store (re-fetches when params change).
 *
 * @param {Function} endpointFn - Function that returns endpoint based on params
 * @param {Object} options - Fetch store options
 */
export function createParamStore(endpointFn, options = {}) {
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
