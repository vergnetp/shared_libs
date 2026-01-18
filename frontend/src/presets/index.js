/**
 * presets/index.js - Pre-configured Auth component settings
 * 
 * Usage:
 *   import { presets, withPreset } from '@myorg/ui/presets'
 *   
 *   <Auth {...presets.internal} />
 *   <Auth {...withPreset('b2b', { ssoButtonText: 'Sign in with Okta' })} />
 */

export const presets = {
  
  /**
   * Internal tools (admin panels, deploy dashboards)
   * - No self-signup
   * - No social login
   * - No terms/privacy
   */
  internal: {
    allowSignup: false,
    allowPasswordReset: false,
    socialProviders: [],
    ssoEnabled: false,
    showTerms: false,
    subtitle: 'Sign in to continue',
  },
  
  /**
   * B2B SaaS (workspace-based, enterprise)
   * - Self-signup with company field
   * - Google/Microsoft social
   * - SSO support
   * - Terms required
   */
  b2b: {
    allowSignup: true,
    allowPasswordReset: true,
    socialProviders: ['google', 'microsoft'],
    ssoEnabled: true,
    ssoButtonText: 'Sign in with SSO',
    showTerms: true,
    signupFields: [
      { name: 'company', label: 'Company Name', required: true, placeholder: 'Acme Inc.' }
    ],
    subtitle: 'Sign in to your workspace',
  },
  
  /**
   * B2C Consumer apps
   * - Self-signup encouraged
   * - Social-first (Google, Apple, Facebook)
   * - Terms required
   */
  b2c: {
    allowSignup: true,
    allowPasswordReset: true,
    socialProviders: ['google', 'apple', 'facebook'],
    socialOnly: false,
    ssoEnabled: false,
    showTerms: true,
    signupFields: [],
    subtitle: 'Welcome back!',
  },
  
  /**
   * B2C Social-only (mobile apps, quick signup)
   * - Social login only, no email/password
   */
  b2cSocialOnly: {
    allowSignup: true,
    socialProviders: ['google', 'apple', 'facebook'],
    socialOnly: true,
    ssoEnabled: false,
    showTerms: true,
    subtitle: 'Sign in to continue',
  },
  
  /**
   * Developer portal (API access)
   * - GitHub + Google social
   * - Use case field
   */
  developer: {
    allowSignup: true,
    allowPasswordReset: true,
    socialProviders: ['github', 'google'],
    ssoEnabled: false,
    showTerms: true,
    signupFields: [
      { name: 'use_case', label: 'What are you building?', required: false, type: 'textarea', placeholder: 'Describe your project...' }
    ],
    subtitle: 'Access the API',
  },
  
  /**
   * Enterprise SSO-only
   * - No email/password, SSO only
   */
  enterpriseSSO: {
    allowSignup: false,
    socialProviders: [],
    socialOnly: true,
    ssoEnabled: true,
    ssoButtonText: 'Sign in with your company account',
    showTerms: false,
    subtitle: 'Use your corporate credentials',
  },
}

/**
 * Merge a preset with custom overrides
 * 
 * @param {string} presetName - Name of preset (internal, b2b, b2c, developer, enterpriseSSO)
 * @param {object} overrides - Custom props to override
 * @returns {object} Merged props
 * 
 * @example
 *   <Auth {...withPreset('b2b', { 
 *     ssoButtonText: 'Sign in with Okta',
 *     signupFields: [
 *       { name: 'company', label: 'Company', required: true },
 *       { name: 'role', label: 'Your Role', required: false }
 *     ]
 *   })} />
 */
export function withPreset(presetName, overrides = {}) {
  const base = presets[presetName]
  if (!base) {
    console.warn(`Unknown preset: ${presetName}. Available: ${Object.keys(presets).join(', ')}`)
    return overrides
  }
  
  // Deep merge signupFields if both exist
  if (overrides.signupFields && base.signupFields) {
    return {
      ...base,
      ...overrides,
      signupFields: [...base.signupFields, ...overrides.signupFields]
    }
  }
  
  return { ...base, ...overrides }
}

export default presets
