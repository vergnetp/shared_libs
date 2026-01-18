<!--
  ToastContainer.svelte - Toast notifications container
  
  Usage:
    <ToastContainer />
    
    // Then in your code:
    import { toasts } from '@myorg/ui'
    toasts.success('Done!')
-->
<script>
  import { fly } from 'svelte/transition'
  import { toasts } from '../stores/toast.js'
</script>

<div class="toast-container">
  {#each $toasts as toast (toast.id)}
    <div 
      class="toast toast-{toast.type}"
      transition:fly={{ x: 100, duration: 200 }}
    >
      <span class="toast-message">{toast.message}</span>
      <button class="toast-close" on:click={() => toasts.remove(toast.id)}>
        ×
      </button>
    </div>
  {/each}
</div>

<style>
  .toast-container {
    position: fixed;
    top: 20px;
    right: 20px;
    z-index: 2000;
    display: flex;
    flex-direction: column;
    gap: 8px;
    max-width: calc(100vw - 40px);
    pointer-events: none;
  }
  
  .toast {
    padding: 12px 16px;
    padding-right: 40px;
    border-radius: 12px;
    background: var(--bg-card);
    border: 1px solid var(--border);
    box-shadow: var(--shadow);
    backdrop-filter: blur(14px);
    font-size: 0.875rem;
    position: relative;
    pointer-events: auto;
    max-width: 400px;
  }
  
  .toast-success {
    border-left: 4px solid var(--success);
  }
  
  .toast-error {
    border-left: 4px solid var(--danger);
  }
  
  .toast-warning {
    border-left: 4px solid var(--warning);
  }
  
  .toast-info {
    border-left: 4px solid var(--primary2);
  }
  
  .toast-message {
    word-break: break-word;
  }
  
  .toast-close {
    position: absolute;
    top: 8px;
    right: 8px;
    width: 24px;
    height: 24px;
    border: none;
    background: transparent;
    color: var(--text-muted);
    cursor: pointer;
    font-size: 1.2rem;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 4px;
  }
  
  .toast-close:hover {
    background: var(--btn-ghost-bg);
    color: var(--text);
  }
  
  @media (max-width: 480px) {
    .toast-container {
      top: 10px;
      right: 10px;
      left: 10px;
      max-width: none;
    }
    
    .toast {
      max-width: none;
    }
  }
</style>
