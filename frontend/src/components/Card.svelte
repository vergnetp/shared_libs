<!--
  Card.svelte - Container card component
  
  Usage:
    <Card title="Settings" glass>Content</Card>
-->
<script>
  export let title = ''
  export let glass = false
  export let padding = true
</script>

<div class="card" class:glass class:no-padding={!padding}>
  {#if title || $$slots.header}
    <div class="card-header">
      {#if title}
        <span class="card-title">{title}</span>
      {/if}
      <slot name="header" />
    </div>
  {/if}
  <div class="card-body">
    <slot />
  </div>
  {#if $$slots.footer}
    <div class="card-footer">
      <slot name="footer" />
    </div>
  {/if}
</div>

<style>
  .card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: var(--r2);
    box-shadow: var(--shadow2);
    overflow: hidden;
  }
  
  .card.glass {
    background: var(--glass-bg);
    backdrop-filter: blur(14px);
    -webkit-backdrop-filter: blur(14px);
    box-shadow: var(--shadow);
    border-radius: var(--r);
  }
  
  .card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 16px 20px;
    border-bottom: 1px solid var(--border);
    gap: 12px;
    flex-wrap: wrap;
  }
  
  .card-title {
    font-size: 1rem;
    font-weight: 600;
  }
  
  .card-body {
    padding: 20px;
  }
  
  .no-padding .card-body {
    padding: 0;
  }
  
  .card-footer {
    padding: 16px 20px;
    border-top: 1px solid var(--border);
    display: flex;
    gap: 8px;
    justify-content: flex-end;
  }
  
  @media (max-width: 640px) {
    .card-header {
      padding: 12px 16px;
    }
    .card-body {
      padding: 16px;
    }
    .no-padding .card-body {
      padding: 0;
    }
  }
</style>
