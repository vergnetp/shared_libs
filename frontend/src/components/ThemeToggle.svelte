<!--
  ThemeToggle.svelte - Theme toggle (dark ↔ light)
  
  Usage:
    <ThemeToggle />
-->
<script>
  import { theme, toggleTheme } from '../hooks/theme.js'
</script>

<button 
  class="theme-toggle" 
  on:click={toggleTheme}
  title={$theme === 'dark' ? 'Switch to light' : 'Switch to dark'}
  aria-label="Toggle theme"
>
  {#if $theme === 'dark'}
    <!-- Sun icon → will switch to light -->
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <circle cx="12" cy="12" r="5"/>
      <path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/>
    </svg>
  {:else}
    <!-- Moon icon → will switch to dark -->
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <path d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z"/>
    </svg>
  {/if}
</button>

<style>
  .theme-toggle {
    width: 36px;
    height: 36px;
    border-radius: 10px;
    border: 1px solid var(--border);
    background: var(--btn-ghost-bg);
    color: var(--text-muted);
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    transition: all 0.15s;
  }
  
  .theme-toggle:hover {
    background: var(--btn-bg-hover);
    border-color: var(--border2);
    color: var(--text);
  }
  
  .theme-toggle svg {
    width: 18px;
    height: 18px;
  }
</style>