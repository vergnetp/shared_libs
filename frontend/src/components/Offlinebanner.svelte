<!--
  OfflineBanner — Shows a dismissible banner when the browser goes offline.
  Auto-hides when back online. Slides in from top.

  @example
    <script>
      import OfflineBanner from '@myorg/ui/components/OfflineBanner.svelte'
    </script>

    <OfflineBanner />

  @example With custom message
    <OfflineBanner message="No connection — changes will sync when you're back online." />
-->
<script>
  import { useOnlineStatus } from '../hooks/online.js'

  /** @type {string} */
  export let message = 'You are offline. Some features may be unavailable.'

  let dismissed = false

  // Auto-show again when status changes (went offline again)
  $: if (!$useOnlineStatus) dismissed = false
</script>

{#if !$useOnlineStatus && !dismissed}
  <div class="offline-banner" role="alert">
    <span class="offline-banner-icon">⚡</span>
    <span class="offline-banner-message">{message}</span>
    <button
      class="offline-banner-dismiss"
      on:click={() => (dismissed = true)}
      aria-label="Dismiss"
    >
      ✕
    </button>
  </div>
{/if}

<style>
  .offline-banner {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    z-index: 9999;
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 10px 16px;
    background: #1a1a2e;
    color: #f0f0f0;
    font-size: 14px;
    font-family: inherit;
    animation: slideDown 0.3s ease-out;
  }

  .offline-banner-icon {
    flex-shrink: 0;
  }

  .offline-banner-message {
    flex: 1;
  }

  .offline-banner-dismiss {
    flex-shrink: 0;
    background: none;
    border: none;
    color: #888;
    cursor: pointer;
    padding: 4px 8px;
    font-size: 14px;
    line-height: 1;
  }

  .offline-banner-dismiss:hover {
    color: #f0f0f0;
  }

  @keyframes slideDown {
    from {
      transform: translateY(-100%);
    }
    to {
      transform: translateY(0);
    }
  }
</style>