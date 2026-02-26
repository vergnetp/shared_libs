<!--
  Tabs.svelte - Tab navigation component
  
  Auto-collapses to hamburger menu on mobile (≤640px) when collapse=true.
  
  Usage:
    <Tabs tabs={[{id: 'a', label: 'Tab A'}]} active="a" on:change={handleChange} />
    <Tabs tabs={...} active="a" collapse={false} />  (never collapse, always tabs)
-->
<script>
  import { createEventDispatcher, onMount, onDestroy } from 'svelte'
  
  export let tabs = []
  export let active = ''
  export let scrollable = true
  export let collapse = true
  
  const dispatch = createEventDispatcher()
  
  let menuOpen = false
  let containerEl
  
  function selectTab(id) {
    active = id
    dispatch('change', id)
  }
  
  function selectMobile(id) {
    selectTab(id)
    menuOpen = false
  }
  
  $: activeLabel = tabs.find(t => t.id === active)?.label || 'Menu'
  
  function handleClickOutside(e) {
    if (menuOpen && containerEl && !containerEl.contains(e.target)) {
      menuOpen = false
    }
  }
  
  onMount(() => document.addEventListener('click', handleClickOutside, true))
  onDestroy(() => document.removeEventListener('click', handleClickOutside, true))
</script>

<div class="tabs-container" class:collapsible={collapse} bind:this={containerEl}>
  <!-- Desktop: normal tabs -->
  <div class="tabs-row" class:scrollable>
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

  <!-- Mobile: hamburger toggle + dropdown -->
  <div class="tabs-mobile">
    <button class="mobile-toggle" on:click|stopPropagation={() => menuOpen = !menuOpen}>
      <span class="hamburger" class:open={menuOpen}>
        <span></span><span></span><span></span>
      </span>
      <span class="mobile-label">{activeLabel}</span>
      <span class="mobile-chevron" class:open={menuOpen}>▾</span>
    </button>
    {#if menuOpen}
      <div class="mobile-dropdown">
        {#each tabs as tab}
          <button
            class="mobile-item"
            class:active={active === tab.id}
            on:click|stopPropagation={() => selectMobile(tab.id)}
          >
            {#if tab.icon}<span class="mobile-item-icon">{tab.icon}</span>{/if}
            <span class="mobile-item-label">{tab.label}</span>
            {#if active === tab.id}<span class="mobile-check">✓</span>{/if}
          </button>
        {/each}
      </div>
    {/if}
  </div>
</div>

<style>
  .tabs-container {
    position: relative;
    margin-top: 8px;
  }

  .tabs-mobile { display: none; }

  /* ============================== */
  /* Desktop tabs                   */
  /* ============================== */
  .tabs-row {
    display: flex;
    gap: 4px;
    padding: 8px;
    background: var(--tabs-bg);
    border: 1px solid var(--border);
    border-radius: var(--r2);
    flex-wrap: wrap;
  }
  
  .tabs-row.scrollable {
    flex-wrap: nowrap;
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
    scrollbar-width: none;
  }

  .tabs-row.scrollable .tab {
    flex-shrink: 0;
  }
  
  .tabs-row.scrollable::-webkit-scrollbar {
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

  /* ============================== */
  /* Mobile hamburger               */
  /* ============================== */
  .mobile-toggle {
    display: flex;
    align-items: center;
    gap: 10px;
    width: 100%;
    background: var(--tabs-bg);
    border: 1px solid var(--border);
    border-radius: var(--r2);
    padding: 10px 14px;
    color: var(--text);
    font-size: 0.875rem;
    font-weight: 600;
    cursor: pointer;
    transition: border-color 0.12s;
  }
  .mobile-toggle:hover { border-color: var(--border2); }

  .hamburger {
    display: flex;
    flex-direction: column;
    gap: 4px;
    width: 18px;
    flex-shrink: 0;
  }
  .hamburger span {
    display: block;
    height: 2px;
    width: 100%;
    background: var(--text-muted);
    border-radius: 1px;
    transition: transform 0.2s, opacity 0.2s;
    transform-origin: center;
  }
  .hamburger.open span:nth-child(1) { transform: translateY(6px) rotate(45deg); }
  .hamburger.open span:nth-child(2) { opacity: 0; }
  .hamburger.open span:nth-child(3) { transform: translateY(-6px) rotate(-45deg); }

  .mobile-label { flex: 1; text-align: left; }
  .mobile-chevron {
    font-size: 0.65rem;
    color: var(--text-muted2);
    transition: transform 0.2s;
  }
  .mobile-chevron.open { transform: rotate(180deg); }

  .mobile-dropdown {
    position: absolute;
    top: calc(100% + 4px);
    left: 0;
    right: 0;
    background: var(--bg-secondary);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 4px 0;
    box-shadow: 0 8px 24px rgba(0,0,0,.4);
    z-index: 200;
    max-height: 60vh;
    overflow-y: auto;
    overscroll-behavior: contain;
  }

  .mobile-dropdown::-webkit-scrollbar {
    width: 4px;
  }
  .mobile-dropdown::-webkit-scrollbar-track {
    background: transparent;
  }
  .mobile-dropdown::-webkit-scrollbar-thumb {
    background: var(--border);
    border-radius: 2px;
  }

  .mobile-item {
    display: flex;
    align-items: center;
    gap: 10px;
    width: 100%;
    padding: 10px 16px;
    border: none;
    background: none;
    color: var(--text-muted);
    font-size: 0.875rem;
    font-weight: 500;
    cursor: pointer;
    text-align: left;
  }
  .mobile-item:hover { background: var(--bg-hover); color: var(--text); }
  .mobile-item.active { color: var(--tab-active-text); font-weight: 600; }
  .mobile-item-icon { font-size: 1rem; }
  .mobile-item-label { flex: 1; }
  .mobile-check { color: #22c55e; font-size: 0.8rem; }

  /* ============================== */
  /* Responsive                     */
  /* ============================== */
  @media (max-width: 768px) {
    .tabs-row {
      padding: 6px;
    }
    .tab {
      padding: 8px 14px;
      font-size: 0.8rem;
    }
  }
  
  @media (max-width: 640px) {
    .collapsible .tabs-row { display: none; }
    .collapsible .tabs-mobile { display: block; }
  }

  @media (max-width: 480px) {
    .tabs-row {
      padding: 4px;
      gap: 2px;
    }
    .tab {
      padding: 7px 10px;
      font-size: 0.75rem;
    }
    .tabs-row:not(.scrollable) .tab {
      flex: 1;
      justify-content: center;
    }
    .tab-icon {
      display: none;
    }
  }
</style>
