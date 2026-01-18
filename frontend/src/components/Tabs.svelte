<!--
  Tabs.svelte - Tab navigation component
  
  Usage:
    <Tabs tabs={[{id: 'a', label: 'Tab A'}]} active="a" on:change={handleChange} />
-->
<script>
  import { createEventDispatcher } from 'svelte'
  
  export let tabs = []        // [{ id: 'tab1', label: 'Tab 1', icon?: '📊' }, ...]
  export let active = ''
  export let scrollable = true
  
  const dispatch = createEventDispatcher()
  
  function selectTab(id) {
    active = id
    dispatch('change', id)
  }
</script>

<div class="tabs" class:scrollable>
  {#each tabs as tab}
    <button
      class="tab"
      class:active={active === tab.id}
      on:click={() => selectTab(tab.id)}
    >
      {#if tab.icon}
        <span class="tab-icon">{tab.icon}</span>
      {/if}
      {tab.label}
    </button>
  {/each}
</div>

<style>
  .tabs {
    display: flex;
    gap: 4px;
    padding: 8px;
    background: var(--tabs-bg);
    border: 1px solid var(--border);
    border-radius: var(--r2);
    flex-wrap: wrap;
  }
  
  .tabs.scrollable {
    flex-wrap: nowrap;
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
    scrollbar-width: none;
  }
  
  .tabs.scrollable::-webkit-scrollbar {
    display: none;
  }
  
  .tab {
    padding: 10px 18px;
    background: transparent;
    border: none;
    border-radius: 12px;
    color: var(--text-muted);
    cursor: pointer;
    font-size: 0.875rem;
    font-weight: 600;
    transition: all 0.2s;
    white-space: nowrap;
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  
  .tab.active {
    background: var(--tab-active-bg);
    color: var(--tab-active-text);
  }
  
  .tab:hover:not(.active) {
    background: var(--tab-hover-bg);
    color: var(--text);
  }
  
  .tab-icon {
    font-size: 1rem;
  }
  
  @media (max-width: 768px) {
    .tabs {
      padding: 6px;
    }
    
    .tab {
      padding: 8px 14px;
      font-size: 0.8rem;
    }
  }
  
  @media (max-width: 480px) {
    .tabs {
      padding: 4px;
      gap: 2px;
    }
    
    .tab {
      padding: 8px 10px;
      font-size: 0.75rem;
      flex: 1;
      justify-content: center;
    }
    
    .tab-icon {
      display: none;
    }
  }
</style>
