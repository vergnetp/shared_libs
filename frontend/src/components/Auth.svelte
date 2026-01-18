<!--
  Auth.svelte - Configurable authentication component
  
  Customization Layers:
  1. CSS Variables - Colors, spacing, radii (via app.css)
  2. Props - Behavioral flags, text, options
  3. Slots - Inject custom content/sections
  4. Presets - B2B, B2C, Internal bundles
  
  Usage:
    <Auth 
      title="My App"
      {...presets.b2b}
      on:success={(e) => handleLogin(e.detail)}
    />
-->
<script>
  import { createEventDispatcher } from 'svelte'
  import { api } from '../api/client.js'
  import { authStore, setAuthToken } from '../stores/auth.js'
  import Button from './Button.svelte'
  
  // =========================================================================
  // Props - Layer 2: Behavioral Configuration
  // =========================================================================
  
  // Branding
  export let title = 'Welcome'
  export let subtitle = 'Sign in to your account'
  export let logo = null              // URL string, or null for brand-dot
  export let showLogo = true
  
  // Behavioral
  export let allowSignup = true
  export let allowPasswordReset = true
  export let requireEmailVerification = false
  
  // Social Login (B2C)
  export let socialProviders = []     // ['google', 'github', 'microsoft', 'apple', 'facebook']
  export let socialOnly = false       // Hide email/password form entirely
  
  // Enterprise (B2B)  
  export let ssoEnabled = false
  export let ssoButtonText = 'Sign in with SSO'
  export let ssoProvider = null       // 'okta', 'azure', 'google-workspace'
  export let ssoUrl = '/api/v1/auth/sso'
  
  // Terms & Privacy (B2C/B2B)
  export let showTerms = false
  export let termsUrl = '/terms'
  export let privacyUrl = '/privacy'
  export let termsText = 'I agree to the'
  
  // Custom signup fields
  export let signupFields = []        // [{name, label, type, required, placeholder, options}]
  
  // API configuration
  export let apiBase = '/api/v1'
  export let loginEndpoint = '/auth/login'
  export let registerEndpoint = '/auth/register'
  export let meEndpoint = '/auth/me'
  
  // Callbacks (alternative to events)
  export let onSuccess = null
  export let onError = null
  
  const dispatch = createEventDispatcher()
  
  // =========================================================================
  // Local State
  // =========================================================================
  
  let activeTab = 'login'
  let loading = false
  let error = null
  
  // Form fields
  let loginEmail = ''
  let loginPassword = ''
  let signupEmail = ''
  let signupPassword = ''
  let signupConfirm = ''
  let acceptedTerms = false
  let customFields = {}
  
  // Initialize custom fields
  $: {
    signupFields.forEach(f => {
      if (!(f.name in customFields)) {
        customFields[f.name] = f.default || ''
      }
    })
  }
  
  // =========================================================================
  // Social Provider Icons
  // =========================================================================
  
  const socialIcons = {
    google: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/><path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/><path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/><path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/></svg>`,
    github: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z"/></svg>`,
    microsoft: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M11.4 11.4H0V0h11.4v11.4z" fill="#F25022"/><path d="M24 11.4H12.6V0H24v11.4z" fill="#7FBA00"/><path d="M11.4 24H0V12.6h11.4V24z" fill="#00A4EF"/><path d="M24 24H12.6V12.6H24V24z" fill="#FFB900"/></svg>`,
    apple: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M18.71 19.5c-.83 1.24-1.71 2.45-3.05 2.47-1.34.03-1.77-.79-3.29-.79-1.53 0-2 .77-3.27.82-1.31.05-2.3-1.32-3.14-2.53C4.25 17 2.94 12.45 4.7 9.39c.87-1.52 2.43-2.48 4.12-2.51 1.28-.02 2.5.87 3.29.87.78 0 2.26-1.07 3.81-.91.65.03 2.47.26 3.64 1.98-.09.06-2.17 1.28-2.15 3.81.03 3.02 2.65 4.03 2.68 4.04-.03.07-.42 1.44-1.38 2.83M13 3.5c.73-.83 1.94-1.46 2.94-1.5.13 1.17-.34 2.35-1.04 3.19-.69.85-1.83 1.51-2.95 1.42-.15-1.15.41-2.35 1.05-3.11z"/></svg>`,
    facebook: `<svg viewBox="0 0 24 24" fill="#1877F2"><path d="M24 12.073c0-6.627-5.373-12-12-12s-12 5.373-12 12c0 5.99 4.388 10.954 10.125 11.854v-8.385H7.078v-3.47h3.047V9.43c0-3.007 1.792-4.669 4.533-4.669 1.312 0 2.686.235 2.686.235v2.953H15.83c-1.491 0-1.956.925-1.956 1.874v2.25h3.328l-.532 3.47h-2.796v8.385C19.612 23.027 24 18.062 24 12.073z"/></svg>`,
  }
  
  const ssoIcons = {
    okta: `<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="12" r="10" fill="#007DC1"/><path d="M12 6a6 6 0 100 12 6 6 0 000-12zm0 9a3 3 0 110-6 3 3 0 010 6z" fill="white"/></svg>`,
    azure: `<svg viewBox="0 0 24 24" fill="currentColor"><path d="M13.05 4.24L6.56 18.05l2.97.01 1.42-3.22h5.09l-.96 3.21h3.03L13.05 4.24z" fill="#0078D4"/></svg>`,
  }
  
  // =========================================================================
  // Handlers
  // =========================================================================
  
  function switchTab(tab) {
    activeTab = tab
    error = null
  }
  
  async function handleLogin(e) {
    e.preventDefault()
    loading = true
    error = null
    
    try {
      const res = await api('POST', loginEndpoint, {
        username: loginEmail,
        password: loginPassword
      }, { skipAuth: true })
      
      const token = res?.access_token || res?.token
      if (!token) throw new Error('Login failed - no token returned')
      
      // Store token
      setAuthToken(token)
      
      // Fetch user info
      let user = res?.user
      if (!user) {
        user = await api('GET', meEndpoint)
      }
      
      if (!user) throw new Error('Login failed - no user info')
      
      authStore.setUser(user)
      
      const result = { user, token }
      dispatch('success', result)
      if (onSuccess) onSuccess(result)
      
    } catch (err) {
      error = err.message
      dispatch('error', { error: err.message, type: 'login' })
      if (onError) onError({ error: err.message, type: 'login' })
    } finally {
      loading = false
    }
  }
  
  async function handleSignup(e) {
    e.preventDefault()
    
    if (signupPassword !== signupConfirm) {
      error = 'Passwords do not match'
      return
    }
    
    if (showTerms && !acceptedTerms) {
      error = 'Please accept the terms and conditions'
      return
    }
    
    loading = true
    error = null
    
    try {
      // Build signup payload with custom fields
      const payload = {
        username: signupEmail,
        email: signupEmail,
        password: signupPassword,
        ...customFields
      }
      
      await api('POST', registerEndpoint, payload, { skipAuth: true })
      
      // Auto-login after signup
      const res = await api('POST', loginEndpoint, {
        username: signupEmail,
        password: signupPassword
      }, { skipAuth: true })
      
      const token = res?.access_token || res?.token
      if (!token) throw new Error('Signup failed - no token returned')
      
      setAuthToken(token)
      
      let user = res?.user
      if (!user) {
        user = await api('GET', meEndpoint)
      }
      
      if (!user) throw new Error('Signup failed - no user info')
      
      authStore.setUser(user)
      
      const result = { user, token }
      dispatch('success', result)
      if (onSuccess) onSuccess(result)
      
    } catch (err) {
      error = err.message
      dispatch('error', { error: err.message, type: 'signup' })
      if (onError) onError({ error: err.message, type: 'signup' })
    } finally {
      loading = false
    }
  }
  
  async function handleSocialLogin(provider) {
    // Redirect to OAuth endpoint
    window.location.href = `${apiBase}/auth/oauth/${provider}`
  }
  
  async function handleSSO() {
    window.location.href = ssoUrl
  }
  
  function handlePasswordReset() {
    dispatch('passwordReset', { email: loginEmail })
  }
</script>

<!-- =========================================================================
     Template - Layer 3: Slots for Custom Content
     ========================================================================= -->

<div class="auth-container">
  <div class="auth-card glass">
    
    <!-- Slot: Custom header -->
    <slot name="header">
      <div class="auth-header">
        {#if showLogo}
          {#if logo}
            <img src={logo} alt={title} class="auth-logo" />
          {:else}
            <span class="brand-dot"></span>
          {/if}
        {/if}
        <h1>{title}</h1>
        <p class="subtitle">{subtitle}</p>
      </div>
    </slot>
    
    <!-- Slot: Before form (announcements, promos) -->
    <slot name="before-form"></slot>
    
    <!-- Social Login Section -->
    {#if socialProviders.length > 0}
      <div class="social-login">
        {#each socialProviders as provider}
          <button 
            class="btn-social btn-social-{provider}"
            on:click={() => handleSocialLogin(provider)}
            disabled={loading}
          >
            <span class="social-icon">{@html socialIcons[provider] || ''}</span>
            Continue with {provider.charAt(0).toUpperCase() + provider.slice(1)}
          </button>
        {/each}
      </div>
      
      {#if !socialOnly}
        <div class="divider">
          <span>or</span>
        </div>
      {/if}
    {/if}
    
    <!-- SSO Section (B2B) -->
    {#if ssoEnabled}
      <button class="btn-sso" on:click={handleSSO} disabled={loading}>
        {#if ssoProvider && ssoIcons[ssoProvider]}
          <span class="sso-icon">{@html ssoIcons[ssoProvider]}</span>
        {/if}
        {ssoButtonText}
      </button>
      
      {#if !socialOnly}
        <div class="divider">
          <span>or use email</span>
        </div>
      {/if}
    {/if}
    
    <!-- Email/Password Forms -->
    {#if !socialOnly}
      
      <!-- Tabs (if signup allowed) -->
      {#if allowSignup}
        <div class="tabs">
          <button 
            class="tab" 
            class:active={activeTab === 'login'}
            on:click={() => switchTab('login')}
          >
            Sign In
          </button>
          <button 
            class="tab" 
            class:active={activeTab === 'signup'}
            on:click={() => switchTab('signup')}
          >
            Sign Up
          </button>
        </div>
      {/if}
      
      <!-- Login Form -->
      {#if activeTab === 'login'}
        <form on:submit={handleLogin}>
          <div class="form-group">
            <label for="login-email">Email</label>
            <input 
              type="email" 
              id="login-email"
              bind:value={loginEmail}
              placeholder="you@example.com" 
              required
              disabled={loading}
              autocomplete="email"
            >
          </div>
          <div class="form-group">
            <label for="login-password">Password</label>
            <input 
              type="password" 
              id="login-password"
              bind:value={loginPassword}
              placeholder="••••••••" 
              required
              disabled={loading}
              autocomplete="current-password"
            >
          </div>
          
          {#if allowPasswordReset}
            <button 
              type="button" 
              class="link-btn forgot-link"
              on:click={handlePasswordReset}
            >
              Forgot password?
            </button>
          {/if}
          
          <Button 
            type="submit" 
            variant="primary" 
            loading={loading}
            disabled={loading}
          >
            Sign In
          </Button>
        </form>
      
      <!-- Signup Form -->
      {:else}
        <form on:submit={handleSignup}>
          <div class="form-group">
            <label for="signup-email">Email</label>
            <input 
              type="email" 
              id="signup-email"
              bind:value={signupEmail}
              placeholder="you@example.com" 
              required
              disabled={loading}
              autocomplete="email"
            >
          </div>
          <div class="form-group">
            <label for="signup-password">Password</label>
            <input 
              type="password" 
              id="signup-password"
              bind:value={signupPassword}
              placeholder="••••••••" 
              required
              disabled={loading}
              autocomplete="new-password"
            >
          </div>
          <div class="form-group">
            <label for="signup-confirm">Confirm Password</label>
            <input 
              type="password" 
              id="signup-confirm"
              bind:value={signupConfirm}
              placeholder="••••••••" 
              required
              disabled={loading}
              autocomplete="new-password"
            >
          </div>
          
          <!-- Custom Signup Fields -->
          {#each signupFields as field}
            <div class="form-group">
              <label for="signup-{field.name}">{field.label}</label>
              {#if field.type === 'select' && field.options}
                <select
                  id="signup-{field.name}"
                  bind:value={customFields[field.name]}
                  required={field.required}
                  disabled={loading}
                >
                  <option value="">Select...</option>
                  {#each field.options as opt}
                    <option value={opt}>{opt}</option>
                  {/each}
                </select>
              {:else if field.type === 'textarea'}
                <textarea
                  id="signup-{field.name}"
                  bind:value={customFields[field.name]}
                  placeholder={field.placeholder || ''}
                  required={field.required}
                  disabled={loading}
                  rows="3"
                ></textarea>
              {:else}
                <input 
                  type="text"
                  id="signup-{field.name}"
                  bind:value={customFields[field.name]}
                  placeholder={field.placeholder || ''}
                  required={field.required}
                  disabled={loading}
                >
              {/if}
            </div>
          {/each}
          
          <!-- Terms checkbox -->
          {#if showTerms}
            <label class="terms-checkbox">
              <input 
                type="checkbox" 
                bind:checked={acceptedTerms} 
                disabled={loading}
              />
              <span>
                {termsText} 
                <a href={termsUrl} target="_blank">Terms</a> and 
                <a href={privacyUrl} target="_blank">Privacy Policy</a>
              </span>
            </label>
          {/if}
          
          <Button 
            type="submit" 
            variant="primary" 
            loading={loading}
            disabled={loading}
          >
            Create Account
          </Button>
        </form>
      {/if}
    {/if}
    
    <!-- Error message -->
    {#if error}
      <p class="error-message">{error}</p>
    {/if}
    
    <!-- Slot: After form (help links, promos) -->
    <slot name="after-form"></slot>
    
    <!-- Slot: Footer -->
    <slot name="footer"></slot>
    
  </div>
</div>

<style>
  .auth-container {
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 100vh;
    padding: 20px;
  }
  
  .auth-card {
    width: 100%;
    max-width: 420px;
    padding: 32px;
  }
  
  .auth-header {
    text-align: center;
    margin-bottom: 24px;
  }
  
  .auth-header h1 {
    font-size: 1.75rem;
    margin-bottom: 8px;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 12px;
  }
  
  .auth-header .subtitle {
    color: var(--text-muted);
  }
  
  .auth-logo {
    height: 40px;
    width: auto;
    margin-bottom: 12px;
  }
  
  .brand-dot {
    width: 12px;
    height: 12px;
    border-radius: 999px;
    background: linear-gradient(180deg, var(--primary), var(--primary2));
    box-shadow: 0 0 0 4px var(--primary-glow, rgba(109,92,255,.18));
  }
  
  /* Tabs */
  .tabs {
    display: flex;
    gap: 4px;
    margin-bottom: 20px;
    padding: 6px;
    background: var(--tabs-bg);
    border: 1px solid var(--border);
    border-radius: 14px;
    justify-content: center;
  }
  
  .tab {
    flex: 1;
    padding: 10px 18px;
    background: transparent;
    border: none;
    border-radius: 10px;
    color: var(--text-muted);
    cursor: pointer;
    font-size: 0.875rem;
    font-weight: 600;
    transition: all 0.2s;
  }
  
  .tab.active {
    background: var(--tab-active-bg);
    color: var(--tab-active-text);
  }
  
  .tab:hover:not(.active) {
    background: var(--tab-hover-bg);
    color: var(--text);
  }
  
  /* Forms */
  .form-group {
    margin-bottom: 16px;
  }
  
  .form-group label {
    display: block;
    font-size: 0.875rem;
    color: var(--text-muted);
    margin-bottom: 6px;
  }
  
  input, select, textarea {
    width: 100%;
    padding: 12px 14px;
    background: var(--bg-input);
    border: 1px solid var(--border);
    border-radius: 14px;
    color: var(--text);
    font-size: 0.9rem;
    transition: all 0.2s;
  }
  
  input:focus, select:focus, textarea:focus {
    outline: none;
    border-color: var(--primary);
    background: var(--input-focus-bg);
  }
  
  input:disabled, select:disabled, textarea:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }
  
  textarea {
    resize: vertical;
    min-height: 80px;
  }
  
  /* Buttons */
  form :global(.btn) {
    width: 100%;
    margin-top: 8px;
  }
  
  .link-btn {
    background: none;
    border: none;
    color: var(--primary);
    cursor: pointer;
    font-size: 0.85rem;
    padding: 0;
    margin-bottom: 12px;
  }
  
  .link-btn:hover {
    text-decoration: underline;
  }
  
  /* Social Login */
  .social-login {
    display: flex;
    flex-direction: column;
    gap: 10px;
    margin-bottom: 16px;
  }
  
  .btn-social {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 10px;
    padding: 12px 16px;
    background: var(--btn-bg);
    border: 1px solid var(--border);
    border-radius: 14px;
    color: var(--text);
    font-size: 0.9rem;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.2s;
  }
  
  .btn-social:hover:not(:disabled) {
    background: var(--btn-bg-hover);
    border-color: var(--border2);
  }
  
  .btn-social:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }
  
  .social-icon {
    width: 20px;
    height: 20px;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  
  .social-icon :global(svg) {
    width: 20px;
    height: 20px;
  }
  
  /* SSO */
  .btn-sso {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 10px;
    width: 100%;
    padding: 14px 16px;
    background: var(--bg-card);
    border: 2px solid var(--primary);
    border-radius: 14px;
    color: var(--primary);
    font-size: 0.95rem;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.2s;
    margin-bottom: 16px;
  }
  
  .btn-sso:hover:not(:disabled) {
    background: var(--primary);
    color: white;
  }
  
  .sso-icon {
    width: 24px;
    height: 24px;
  }
  
  .sso-icon :global(svg) {
    width: 24px;
    height: 24px;
  }
  
  /* Divider */
  .divider {
    display: flex;
    align-items: center;
    margin: 16px 0;
    color: var(--text-muted2);
    font-size: 0.8rem;
  }
  
  .divider::before,
  .divider::after {
    content: '';
    flex: 1;
    height: 1px;
    background: var(--border);
  }
  
  .divider span {
    padding: 0 12px;
  }
  
  /* Terms */
  .terms-checkbox {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    font-size: 0.85rem;
    color: var(--text-muted);
    margin: 16px 0;
    cursor: pointer;
  }
  
  .terms-checkbox input {
    width: auto;
    margin-top: 2px;
  }
  
  .terms-checkbox a {
    color: var(--primary);
    text-decoration: none;
  }
  
  .terms-checkbox a:hover {
    text-decoration: underline;
  }
  
  /* Error */
  .error-message {
    color: var(--danger);
    margin-top: 16px;
    text-align: center;
    font-size: 0.875rem;
  }
  
  /* Mobile */
  @media (max-width: 480px) {
    .auth-card {
      padding: 24px 20px;
    }
    
    .auth-header h1 {
      font-size: 1.5rem;
    }
  }
</style>
