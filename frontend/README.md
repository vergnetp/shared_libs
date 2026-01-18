# @myorg/ui - Shared Svelte UI Components

Reusable Svelte components, stores, and utilities for building consistent UIs across all apps.

## Installation

In your app's `package.json`:

```json
{
  "dependencies": {
    "@myorg/ui": "workspace:*"
  }
}
```

Then run `npm install` from the workspace root.

## Quick Start

```svelte
<script>
  import { Auth, Header, ToastContainer } from '@myorg/ui'
  import { authStore, isAuthenticated } from '@myorg/ui'
  import { presets } from '@myorg/ui/presets'
  import '@myorg/ui/styles/base.css'
  import './app.css'  // Your theme overrides
</script>

{#if !$isAuthenticated}
  <Auth 
    title="My App"
    {...presets.internal}
    on:success={(e) => console.log(e.detail)}
  />
{:else}
  <Header title="My App" />
  <main>...</main>
{/if}

<ToastContainer />
```

## Components

| Component | Description |
|-----------|-------------|
| `Auth` | Full authentication form (login/signup/social/SSO) |
| `Header` | App header with branding, user info, logout |
| `Button` | Styled button with variants |
| `Badge` | Status badges |
| `Card` | Container card with header/footer slots |
| `Modal` | Dialog modal |
| `Tabs` | Tab navigation |
| `ToastContainer` | Toast notification display |
| `ThemeToggle` | Dark/light mode toggle |

## Auth Presets

Pre-configured settings for different app types:

```javascript
import { presets, withPreset } from '@myorg/ui/presets'

// Use preset directly
<Auth {...presets.internal} />
<Auth {...presets.b2b} />
<Auth {...presets.b2c} />
<Auth {...presets.developer} />

// Customize a preset
<Auth {...withPreset('b2b', { 
  ssoButtonText: 'Sign in with Okta',
  signupFields: [
    { name: 'company', label: 'Company', required: true }
  ]
})} />
```

### Preset Summary

| Preset | Signup | Social | SSO | Terms | Use Case |
|--------|--------|--------|-----|-------|----------|
| `internal` | ❌ | ❌ | ❌ | ❌ | Admin panels, internal tools |
| `b2b` | ✅ | Google, Microsoft | ✅ | ✅ | SaaS, enterprise apps |
| `b2c` | ✅ | Google, Apple, Facebook | ❌ | ✅ | Consumer apps |
| `b2cSocialOnly` | ✅ | All | ❌ | ✅ | Mobile, quick signup |
| `developer` | ✅ | GitHub, Google | ❌ | ✅ | API/developer portals |
| `enterpriseSSO` | ❌ | ❌ | ✅ | ❌ | SSO-only enterprise |

## Theming

Override CSS variables in your `app.css`:

```css
/* Teal theme example */
:root {
  --primary: #14b8a6;
  --primary2: #06b6d4;
  --primary-glow: rgba(20,184,166,.18);
  --primary-border: rgba(20,184,166,.35);
  --primary-shadow: rgba(20,184,166,.25);
}
```

### Available Variables

**Colors:** `--primary`, `--primary2`, `--success`, `--warning`, `--danger`

**Backgrounds:** `--bg0`, `--bg1`, `--bg-card`, `--bg-input`

**Text:** `--text`, `--text-muted`, `--text-muted2`

**Borders:** `--border`, `--border2`

**Radii:** `--r` (large), `--r2` (small)

## Stores

```javascript
import { 
  authStore, isAuthenticated, currentUser, isAdmin,
  setAuthToken, clearAuth,
  toasts,
  theme, setTheme, toggleTheme
} from '@myorg/ui'

// Auth
$isAuthenticated  // boolean
$currentUser      // user object
setAuthToken('...')
clearAuth()

// Toasts
toasts.success('Saved!')
toasts.error('Failed', 5000)

// Theme
$theme  // 'dark' | 'light'
toggleTheme()
```

## API Client

```javascript
import { api, setApiConfig } from '@myorg/ui'

// Configure once
setApiConfig({ baseUrl: '/api/v1' })

// Use
const users = await api('GET', '/users')
await api('POST', '/users', { name: 'John' })
```

## Project Structure

```
shared_libs/frontend/
├── package.json
└── src/
    ├── index.js          # Main exports
    ├── components/       # Svelte components
    ├── stores/           # Svelte stores
    ├── api/              # API client
    ├── styles/           # CSS
    └── presets/          # Auth presets
```
