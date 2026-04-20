/**
 * Design System Tokens
 *
 * Consistent spacing, typography, and visual hierarchy across the app.
 * All values based on 8px base unit for consistency.
 */

// ═════════════════════════════════════════════════════════════════════
// TYPOGRAPHY SCALE
// ═════════════════════════════════════════════════════════════════════

export const TYPOGRAPHY = {
  h1: {
    size: '2rem',      // 32px
    weight: 700,
    lineHeight: '1.2',
    letterSpacing: '-0.02em',
  },
  h2: {
    size: '1.5rem',    // 24px
    weight: 700,
    lineHeight: '1.3',
    letterSpacing: '-0.01em',
  },
  h3: {
    size: '1.125rem',  // 18px
    weight: 600,
    lineHeight: '1.4',
  },
  h4: {
    size: '1rem',      // 16px
    weight: 600,
    lineHeight: '1.5',
  },
  body: {
    size: '0.875rem',  // 14px
    weight: 400,
    lineHeight: '1.5',
  },
  bodySmall: {
    size: '0.8125rem', // 13px
    weight: 400,
    lineHeight: '1.5',
  },
  caption: {
    size: '0.75rem',   // 12px
    weight: 500,
    lineHeight: '1.4',
  },
  label: {
    size: '0.75rem',   // 12px
    weight: 600,
    lineHeight: '1.4',
    letterSpacing: '0.005em',
  },
  code: {
    size: '0.8125rem', // 13px
    weight: 500,
    lineHeight: '1.5',
    fontFamily: '"Fira Code", "Monaco", monospace',
  },
} as const

// ═════════════════════════════════════════════════════════════════════
// SPACING SCALE (8px base)
// ═════════════════════════════════════════════════════════════════════

export const SPACING = {
  none: '0',
  xs: '0.25rem',    // 4px   - tight spacing
  sm: '0.5rem',     // 8px   - small gaps
  md: '1rem',       // 16px  - default spacing
  lg: '1.5rem',     // 24px  - large spacing
  xl: '2rem',       // 32px  - extra large
  '2xl': '3rem',    // 48px  - page margins
  '3xl': '4rem',    // 64px  - hero sections
} as const

// Common spacing combinations
export const SPACING_PATTERNS = {
  compact: { x: SPACING.xs, y: SPACING.xs },    // 4px
  normal: { x: SPACING.sm, y: SPACING.sm },     // 8px
  default: { x: SPACING.md, y: SPACING.md },    // 16px
  spacious: { x: SPACING.lg, y: SPACING.lg },   // 24px
  pageMargin: { x: SPACING['2xl'], y: SPACING.xl }, // 48px x 32px
} as const

// ═════════════════════════════════════════════════════════════════════
// BORDER RADIUS
// ═════════════════════════════════════════════════════════════════════

export const RADIUS = {
  none: '0',
  sm: '0.25rem',    // 4px   - tight corners
  md: '0.5rem',     // 8px   - default
  lg: '0.75rem',    // 12px  - larger elements
  full: '9999px',   // full  - pills
} as const

// ═════════════════════════════════════════════════════════════════════
// SHADOWS
// ═════════════════════════════════════════════════════════════════════

export const SHADOWS = {
  none: 'none',
  xs: '0 1px 2px 0 rgb(0 0 0 / 0.05)',
  sm: '0 1px 3px 0 rgb(0 0 0 / 0.1), 0 1px 2px -1px rgb(0 0 0 / 0.1)',
  md: '0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1)',
  lg: '0 10px 15px -3px rgb(0 0 0 / 0.1), 0 4px 6px -4px rgb(0 0 0 / 0.1)',
  xl: '0 20px 25px -5px rgb(0 0 0 / 0.1), 0 8px 10px -6px rgb(0 0 0 / 0.1)',
} as const

// ═════════════════════════════════════════════════════════════════════
// TRANSITIONS
// ═════════════════════════════════════════════════════════════════════

export const TRANSITIONS = {
  fast: '150ms cubic-bezier(0.4, 0, 0.2, 1)',
  default: '200ms cubic-bezier(0.4, 0, 0.2, 1)',
  slow: '300ms cubic-bezier(0.4, 0, 0.2, 1)',
} as const

// ═════════════════════════════════════════════════════════════════════
// Z-INDEX SCALE
// ═════════════════════════════════════════════════════════════════════

export const Z_INDEX = {
  base: 0,
  dropdown: 100,
  sticky: 200,
  fixed: 300,
  modal: 400,
  popover: 500,
  tooltip: 600,
} as const

// ═════════════════════════════════════════════════════════════════════
// BREAKPOINTS (for responsive design)
// ═════════════════════════════════════════════════════════════════════

export const BREAKPOINTS = {
  xs: '320px',
  sm: '640px',
  md: '768px',
  lg: '1024px',
  xl: '1280px',
  '2xl': '1536px',
} as const

// ═════════════════════════════════════════════════════════════════════
// LAYOUT CONSTANTS
// ═════════════════════════════════════════════════════════════════════

export const LAYOUT = {
  sidebarWidth: '15rem',        // 240px
  sidebarCollapsedWidth: '4rem', // 64px
  topbarHeight: '3.5rem',        // 56px
  maxContentWidth: '1400px',
} as const

// ═════════════════════════════════════════════════════════════════════
// CSS UTILITIES (for use in className strings)
// ═════════════════════════════════════════════════════════════════════

export const CSS = {
  flexCenter: 'flex items-center justify-center',
  flexBetween: 'flex items-center justify-between',
  flexStart: 'flex items-start justify-start',
  flexCol: 'flex flex-col',
  absolute: 'absolute inset-0',
  visuallyHidden: 'sr-only',
  truncate: 'truncate',
  truncateLine: 'line-clamp-1',
  truncateTwoLines: 'line-clamp-2',
  truncateThreeLines: 'line-clamp-3',
} as const

// ═════════════════════════════════════════════════════════════════════
// COMPONENT SIZING PRESETS
// ═════════════════════════════════════════════════════════════════════

export const COMPONENT_SIZES = {
  button: {
    xs: { height: '1.5rem', padding: '0.25rem 0.5rem', fontSize: '0.75rem' },    // 24px
    sm: { height: '1.75rem', padding: '0.375rem 0.75rem', fontSize: '0.8125rem' }, // 28px
    md: { height: '2.25rem', padding: '0.5rem 1rem', fontSize: '0.875rem' },       // 36px
    lg: { height: '2.75rem', padding: '0.625rem 1.5rem', fontSize: '1rem' },       // 44px
    xl: { height: '3rem', padding: '0.75rem 2rem', fontSize: '1rem' },             // 48px
  },
  input: {
    xs: { height: '1.5rem', padding: '0.25rem 0.5rem', fontSize: '0.75rem' },
    sm: { height: '1.75rem', padding: '0.375rem 0.75rem', fontSize: '0.8125rem' },
    md: { height: '2.25rem', padding: '0.5rem 1rem', fontSize: '0.875rem' },
    lg: { height: '2.75rem', padding: '0.625rem 1rem', fontSize: '1rem' },
  },
  card: {
    compact: { padding: '0.5rem' },      // 8px
    normal: { padding: '1rem' },         // 16px
    spacious: { padding: '1.5rem' },     // 24px
    large: { padding: '2rem' },          // 32px
  },
  gap: {
    compact: '0.5rem',   // 8px
    normal: '1rem',      // 16px
    spacious: '1.5rem',  // 24px
    large: '2rem',       // 32px
  },
} as const

// ═════════════════════════════════════════════════════════════════════
// MOTION / ANIMATION PRESETS
// ═════════════════════════════════════════════════════════════════════

export const ANIMATIONS = {
  fadeIn: {
    animation: 'fadeIn 200ms ease-in-out',
  },
  slideIn: {
    animation: 'slideIn 300ms ease-out',
  },
  scaleIn: {
    animation: 'scaleIn 200ms ease-out',
  },
  spin: {
    animation: 'spin 1s linear infinite',
  },
} as const

// ═════════════════════════════════════════════════════════════════════
// ACCESSIBILITY HELPERS
// ═════════════════════════════════════════════════════════════════════

export const A11Y = {
  focusRing: 'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary',
  focusRingOffset: 'focus-visible:outline-offset-2',
  reducedMotion: '@media (prefers-reduced-motion: reduce)',
} as const
