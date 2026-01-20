<!--
  Modal.svelte - Dialog modal component
  
  Usage:
    <Modal bind:open title="Confirm">Are you sure?</Modal>
-->
<script>
  import { createEventDispatcher } from 'svelte'
  import { fade, scale } from 'svelte/transition'
  
  export let open = false
  export let title = ''
  export let width = '500px'
  export let closeOnOverlay = true
  
  const dispatch = createEventDispatcher()
  
  function close() {
    open = false
    dispatch('close')
  }
  
  function handleOverlayClick(e) {
    if (closeOnOverlay && e.target === e.currentTarget) {
      close()
    }
  }
  
  function handleKeydown(e) {
    if (e.key === 'Escape') {
      close()
    }
  }
</script>

<svelte:window on:keydown={handleKeydown} />

{#if open}
  <div 
    class="modal-overlay" 
    transition:fade={{ duration: 150 }}
    on:click={handleOverlayClick}
    role="dialog"
    aria-modal="true"
  >
    <div 
      class="modal" 
      style="max-width: {width};"
      transition:scale={{ duration: 200, start: 0.95 }}
    >
      {#if title || $$slots.header}
        <div class="modal-header">
          {#if title}
            <h3>{title}</h3>
          {/if}
          <slot name="header" />
          <button class="modal-close" on:click={close} aria-label="Close">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </div>
      {/if}
      <div class="modal-body">
        <slot />
      </div>
      {#if $$slots.footer}
        <div class="modal-footer">
          <slot name="footer" />
        </div>
      {/if}
    </div>
  </div>
{/if}

<style>
  .modal-overlay {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background: rgba(0,0,0,0.85);
    backdrop-filter: blur(8px);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 1000;
    padding: 20px;
  }
  
  .modal-overlay ~ .modal-overlay {
    z-index: 1100;
  }
  
  .modal {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: var(--r);
    width: 100%;
    max-height: 90vh;
    overflow: hidden;
    box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
    display: flex;
    flex-direction: column;
  }
  
  .modal-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 16px 20px;
    border-bottom: 1px solid var(--border);
    gap: 12px;
  }
  
  .modal-header h3 {
    margin: 0;
    font-size: 1.1rem;
  }
  
  .modal-close {
    width: 32px;
    height: 32px;
    border: none;
    background: var(--btn-ghost-bg);
    border-radius: 8px;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--text-muted);
    transition: all 0.15s;
    flex-shrink: 0;
  }
  
  .modal-close:hover {
    background: var(--btn-bg-hover);
    color: var(--text);
  }
  
  .modal-close svg {
    width: 18px;
    height: 18px;
  }
  
  .modal-body {
    padding: 20px;
    overflow-y: auto;
    flex: 1;
  }
  
  .modal-footer {
    padding: 16px 20px;
    border-top: 1px solid var(--border);
    display: flex;
    gap: 8px;
    justify-content: flex-end;
  }
  
  @media (max-width: 640px) {
    .modal-overlay {
      padding: 10px;
      align-items: flex-end;
    }
    
    .modal {
      max-height: 85vh;
      border-radius: var(--r2) var(--r2) 0 0;
    }
  }
</style>
