<!--
  Badge.svelte - Status badge component
  
  Usage:
    <Badge variant="success">Active</Badge>
-->
<script>
  export let variant = 'info' // success, warning, danger, error, info, purple, secondary
</script>

<span class="badge badge-{variant}">
  <slot />
</span>

<style>
  .badge {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    padding: 4px 10px;
    border-radius: 8px;
    font-size: 0.75rem;
    font-weight: 650;
    white-space: nowrap;
    min-width: 60px;
    text-align: center;
  }
  
  .badge-success {
    background: rgba(54,211,124,.15);
    color: var(--success);
  }
  
  .badge-warning {
    background: rgba(245,158,11,.15);
    color: var(--warning);
  }
  
  .badge-danger,
  .badge-error {
    background: rgba(255,77,94,.15);
    color: var(--danger);
  }
  
  .badge-info {
    background: rgba(45,125,255,.15);
    color: var(--primary2);
  }
  
  .badge-purple {
    background: rgba(147,51,234,.15);
    color: #a855f7;
  }
  
  .badge-secondary {
    background: rgba(148,163,184,.15);
    color: #94a3b8;
  }
</style>
