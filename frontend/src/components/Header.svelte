<!--
  Header.svelte - App header with branding and user info
  
  Usage:
    <Header 
      title="My App" 
      showUser={true}
      showThemeToggle={true}
      on:logout={handleLogout}
    />
-->
<script>
  import { createEventDispatcher } from 'svelte'
  import { useAuth, clearAuth } from '../hooks/auth.js'
  import Button from './Button.svelte'
  import ThemeToggle from './ThemeToggle.svelte'
  
  export let title = 'Dashboard'
  export let logo = null              // URL or null for brand-dot
  export let showLogo = true
  export let showUser = true
  export let showThemeToggle = false
  export let showLogout = true
  export let logoutText = 'Logout'
  
  // For mock mode indicator
  export let showMockBadge = false
  
  const dispatch = createEventDispatcher()
  
  // Auto-detect mock mode
  let mockEnabled = showMockBadge
  try {
    if (typeof window !== 'undefined') {
      mockEnabled = showMockBadge || 
        (import.meta?.env?.VITE_MOCK_API === '1') || 
        localStorage.getItem('mockApi') === '1'
    }
  } catch (e) {
    mockEnabled = showMockBadge
  }
  
  function logout() {
    clearAuth()
    dispatch('logout')
  }
</script>

<header class="header glass">
  <div class="header-brand">
    {#if showLogo && logo}
      <img src={logo} alt={title} class="header-logo" />
    {/if}
    <h1>{title}</h1>
    
    <!-- Slot for custom content after title -->
    <slot name="after-title"></slot>
  </div>
  
  <div class="header-actions">
    <!-- Slot for custom actions -->
    <slot name="actions"></slot>
    
    {#if mockEnabled}
      <span class="mock-badge">MOCK</span>
    {/if}
    
    {#if showThemeToggle}
      <ThemeToggle />
    {/if}
    
    {#if showUser && $useAuth.user}
      <span class="user-email">{$useAuth.user.email || $useAuth.user.username}</span>
    {/if}
    
    {#if showLogout && $useAuth.token}
      <Button variant="ghost" size="sm" on:click={logout}>{logoutText}</Button>
    {/if}
  </div>
</header>

<style>
  .header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 16px 24px;
    margin-bottom: 16px;
    gap: 16px;
  }
  
  .header-brand {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  
  h1 {
    margin: 0;
    font-size: 1.25rem;
    font-weight: 800;
    white-space: nowrap;
  }
  
  .header-logo {
    height: 28px;
    width: auto;
  }
  
  .brand-dot {
    width: 10px;
    height: 10px;
    border-radius: 999px;
    background: linear-gradient(180deg, var(--primary), var(--primary2));
    box-shadow: 0 0 0 4px var(--primary-glow, rgba(109,92,255,.18));
    flex-shrink: 0;
  }
  
  .header-actions {
    display: flex;
    gap: 12px;
    align-items: center;
  }
  
  .mock-badge {
    font-size: 12px;
    font-weight: 800;
    letter-spacing: .6px;
    padding: 6px 10px;
    border-radius: 999px;
    border: 1px solid rgba(109,92,255,.35);
    background: rgba(109,92,255,.14);
    color: rgba(220,220,255,.95);
  }
  
  .user-email {
    color: var(--text-muted);
    font-size: 0.875rem;
  }
  
  @media (max-width: 640px) {
    .header {
      padding: 10px 12px;
      gap: 8px;
    }
    
    h1 {
      font-size: 1rem;
    }
    
    .header-actions {
      gap: 8px;
    }

    .user-email {
      display: none;
    }
  }

  @media (max-width: 380px) {
    .header {
      padding: 8px 10px;
      gap: 6px;
    }

    h1 {
      font-size: 0.9rem;
    }

    .header-actions {
      gap: 6px;
    }
  }
</style>
