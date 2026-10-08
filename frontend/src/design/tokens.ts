/**
 * Miroir typé des jetons déclarés dans `tokens.css`.
 *
 * Objectif : pouvoir référencer un jeton depuis TypeScript (style inline,
 * valeur passée à un composant, variante calculée) sans jamais écrire une
 * couleur ou une taille en dur. La source de vérité des VALEURS reste le CSS ;
 * ce module n'expose que les NOMS, sous forme de `var(--…)`.
 *
 * @example
 * <div style={{ borderColor: color.borderStrong, padding: space[4] }} />
 */

export type CssVar = `var(--${string})`;

/** Couleurs sémantiques : elles suivent automatiquement le thème actif. */
export const color = {
  canvas: 'var(--color-canvas)',
  canvasVeil: 'var(--color-canvas-veil)',
  surface: 'var(--color-surface)',
  surfaceRaised: 'var(--color-surface-raised)',
  surfaceSunken: 'var(--color-surface-sunken)',
  surfaceHover: 'var(--color-surface-hover)',

  border: 'var(--color-border)',
  borderSubtle: 'var(--color-border-subtle)',
  borderStrong: 'var(--color-border-strong)',

  text: 'var(--color-text)',
  textMuted: 'var(--color-text-muted)',
  textSubtle: 'var(--color-text-subtle)',
  textInverse: 'var(--color-text-inverse)',

  accent: 'var(--color-accent)',
  accentHover: 'var(--color-accent-hover)',
  accentSolid: 'var(--color-accent-solid)',
  accentSolidHover: 'var(--color-accent-solid-hover)',
  accentContrast: 'var(--color-accent-contrast)',
  accentSurface: 'var(--color-accent-surface)',
  accentBorder: 'var(--color-accent-border)',

  success: 'var(--color-success)',
  successSurface: 'var(--color-success-surface)',
  warning: 'var(--color-warning)',
  warningSurface: 'var(--color-warning-surface)',
  danger: 'var(--color-danger)',
  dangerSurface: 'var(--color-danger-surface)',

  focusRing: 'var(--color-focus-ring)',
} as const satisfies Record<string, CssVar>;

/** Échelle d'espacement (base 4px). La clé est le multiple de 0.25rem. */
export const space = {
  0: 'var(--rag-space-0)',
  1: 'var(--rag-space-1)',
  2: 'var(--rag-space-2)',
  3: 'var(--rag-space-3)',
  4: 'var(--rag-space-4)',
  5: 'var(--rag-space-5)',
  6: 'var(--rag-space-6)',
  8: 'var(--rag-space-8)',
  10: 'var(--rag-space-10)',
  12: 'var(--rag-space-12)',
  16: 'var(--rag-space-16)',
  20: 'var(--rag-space-20)',
  24: 'var(--rag-space-24)',
} as const satisfies Record<number, CssVar>;

export const radius = {
  xs: 'var(--rag-radius-xs)',
  sm: 'var(--rag-radius-sm)',
  md: 'var(--rag-radius-md)',
  lg: 'var(--rag-radius-lg)',
  xl: 'var(--rag-radius-xl)',
  full: 'var(--rag-radius-full)',
} as const satisfies Record<string, CssVar>;

export const fontFamily = {
  display: 'var(--rag-font-display)',
  sans: 'var(--rag-font-sans)',
  mono: 'var(--rag-font-mono)',
} as const satisfies Record<string, CssVar>;

export const fontSize = {
  '2xs': 'var(--rag-text-2xs)',
  xs: 'var(--rag-text-xs)',
  sm: 'var(--rag-text-sm)',
  base: 'var(--rag-text-base)',
  md: 'var(--rag-text-md)',
  lg: 'var(--rag-text-lg)',
  xl: 'var(--rag-text-xl)',
  '2xl': 'var(--rag-text-2xl)',
  '3xl': 'var(--rag-text-3xl)',
} as const satisfies Record<string, CssVar>;

export const lineHeight = {
  tight: 'var(--rag-leading-tight)',
  snug: 'var(--rag-leading-snug)',
  normal: 'var(--rag-leading-normal)',
  relaxed: 'var(--rag-leading-relaxed)',
} as const satisfies Record<string, CssVar>;

export const fontWeight = {
  regular: 'var(--rag-weight-regular)',
  medium: 'var(--rag-weight-medium)',
  semibold: 'var(--rag-weight-semibold)',
  bold: 'var(--rag-weight-bold)',
} as const satisfies Record<string, CssVar>;

export const tracking = {
  tighter: 'var(--rag-tracking-tighter)',
  tight: 'var(--rag-tracking-tight)',
  normal: 'var(--rag-tracking-normal)',
  wide: 'var(--rag-tracking-wide)',
  caps: 'var(--rag-tracking-caps)',
} as const satisfies Record<string, CssVar>;

export const shadow = {
  sm: 'var(--shadow-sm)',
  md: 'var(--shadow-md)',
  lg: 'var(--shadow-lg)',
} as const satisfies Record<string, CssVar>;

export const zIndex = {
  base: 'var(--rag-z-base)',
  sticky: 'var(--rag-z-sticky)',
  overlay: 'var(--rag-z-overlay)',
  modal: 'var(--rag-z-modal)',
  toast: 'var(--rag-z-toast)',
} as const satisfies Record<string, CssVar>;

export const duration = {
  instant: 'var(--rag-duration-instant)',
  fast: 'var(--rag-duration-fast)',
  base: 'var(--rag-duration-base)',
  slow: 'var(--rag-duration-slow)',
} as const satisfies Record<string, CssVar>;

export const easing = {
  standard: 'var(--rag-ease-standard)',
  out: 'var(--rag-ease-out)',
  inOut: 'var(--rag-ease-in-out)',
} as const satisfies Record<string, CssVar>;

export const layout = {
  containerMax: 'var(--rag-container-max)',
  containerNarrow: 'var(--rag-container-narrow)',
  headerHeight: 'var(--rag-header-height)',
} as const satisfies Record<string, CssVar>;

export const tokens = {
  color,
  space,
  radius,
  fontFamily,
  fontSize,
  lineHeight,
  fontWeight,
  tracking,
  shadow,
  zIndex,
  duration,
  easing,
  layout,
} as const;

export type ColorToken = keyof typeof color;
export type SpaceToken = keyof typeof space;
export type RadiusToken = keyof typeof radius;
export type FontFamilyToken = keyof typeof fontFamily;
export type FontSizeToken = keyof typeof fontSize;
export type ShadowToken = keyof typeof shadow;
export type DurationToken = keyof typeof duration;
