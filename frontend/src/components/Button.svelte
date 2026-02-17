<!--
  Button.svelte - Configurable button component
  
  Usage:
    <Button variant="primary" on:click={handleClick}>Click me</Button>
    <Button href="/page" variant="ghost">Link</Button>
-->
<script>
  export let variant = 'default' // default, primary, success, danger, warning, ghost
  export let size = 'md'         // sm, md, lg
  export let disabled = false
  export let loading = false
  export let full = false
  export let pill = false
  export let iconOnly = false
  export let type = 'button'
  export let href = null
  export let title = undefined
  export let ariaLabel = undefined
</script>

{#if href}
  <a 
    {href}
    {title}
    aria-label={ariaLabel}
    class={`btn btn-${variant} btn-${size}`}
    class:disabled
    class:full
    class:pill
    class:iconOnly
    on:click
  >
    {#if loading}
      <span class="spinner"></span>
    {/if}
    <slot />
  </a>
{:else}
  <button
    {type}
    {title}
    aria-label={ariaLabel}
    class={`btn btn-${variant} btn-${size}`}
    disabled={disabled || loading}
    class:disabled
    on:click
  >
    {#if loading}
      <span class="spinner"></span>
    {/if}
    <slot />
  </button>
{/if}

<style>

  .btn {
    height: var(--btn-h, 40px);
    padding: var(--btn-pad, 0 14px);
    border: 1px solid var(--border);
    border-radius: var(--btn-r, 14px);
    font-size: 0.875rem;
    font-weight: 650;
    cursor: pointer;
    transition: transform 0.12s ease, background 0.12s ease, border-color 0.12s ease, box-shadow 0.12s ease, filter 0.12s ease;
    background: var(--btn-bg, rgba(255,255,255,.06));
    color: var(--text);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 10px;
    text-decoration: none;
    white-space: nowrap;
    box-shadow: 0 10px 26px rgba(0,0,0,0.35);
    user-select: none;
  }

  .btn:hover:not(.disabled):not(:disabled) {
    background: var(--btn-bg-hover, rgba(255,255,255,.10));
    border-color: var(--border2);
    transform: translateY(-1px);
    box-shadow: 0 14px 34px rgba(0,0,0,0.42);
  }

  .btn:active:not(.disabled):not(:disabled) {
    transform: translateY(0px);
    box-shadow: 0 10px 26px rgba(0,0,0,0.35);
  }

  .btn:focus-visible {
    outline: none;
    box-shadow:
      0 0 0 3px rgba(109,92,255,0.20),
      0 10px 26px rgba(0,0,0,0.35);
  }

  /* Variants */
  .btn-default,
  .btn-secondary {
    background: var(--btn-bg, rgba(255,255,255,.06));
    color: var(--text);
  }

  .btn-primary {
    background: linear-gradient(135deg, var(--primary), var(--primary2));
    border-color: rgba(109,92,255,.38);
    color: white;
    box-shadow:
      0 12px 30px rgba(0,0,0,0.42),
      0 0 28px rgba(109,92,255,0.28);
  }

  .btn-primary:hover:not(.disabled):not(:disabled) {
    filter: brightness(1.06);
    box-shadow:
      0 14px 36px rgba(0,0,0,0.48),
      0 0 36px rgba(109,92,255,0.36);
  }

  .btn-success {
    background: rgba(54,211,124,.16);
    border-color: rgba(54,211,124,.35);
    color: var(--success);
  }
  .btn-success:hover:not(.disabled):not(:disabled) {
    background: rgba(54,211,124,.26);
  }

  .btn-danger {
    background: rgba(255,77,94,.06);
    border-color: rgba(255,77,94,.35);
    color: rgba(255,180,186,.95);
  }
  .btn-danger:hover:not(.disabled):not(:disabled) {
    background: rgba(255,77,94,.12);
  }

  .btn-warning {
    background: rgba(245,158,11,.10);
    border-color: rgba(245,158,11,.35);
    color: rgba(255,214,153,.95);
  }
  .btn-warning:hover:not(.disabled):not(:disabled) {
    background: rgba(245,158,11,.16);
  }

  .btn-ghost {
    background: transparent;
    border-color: rgba(255,255,255,.08);
    box-shadow: none;
  }
  .btn-ghost:hover:not(.disabled):not(:disabled) {
    background: rgba(255,255,255,.06);
    box-shadow: 0 12px 28px rgba(0,0,0,0.32);
  }

  /* Sizes */
  .btn-sm { height: 34px; padding: 0 12px; font-size: 0.82rem; border-radius: 12px; gap: 8px; }
  .btn-md { height: var(--btn-h, 40px); }
  .btn-lg { height: 48px; padding: 0 18px; font-size: 0.95rem; border-radius: 16px; gap: 10px; }

  /* Modifiers */
  .full { width: 100%; }
  .pill { border-radius: 999px; }
  .iconOnly { width: 36px; padding: 0; gap: 0; }
  .btn-sm.iconOnly { width: 34px; }
  .btn-lg.iconOnly { width: 48px; }

  .disabled,
  .btn:disabled {
    opacity: 0.55;
    cursor: not-allowed;
    transform: none !important;
    box-shadow: none !important;
  }

  .spinner {
    width: 16px;
    height: 16px;
    border: 2px solid rgba(255,255,255,.22);
    border-top-color: rgba(255,255,255,.85);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }


  @keyframes spin { to { transform: rotate(360deg); } }

</style>
