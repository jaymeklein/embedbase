/** @type {import('tailwindcss').Config} */

// Colors are driven by CSS variables holding RGB *channels* (e.g. "26 26 24"),
// wrapped here so Tailwind's opacity modifiers (bg-ink/20, border-ok/30) work.
const withAlpha = (cssVar) => `rgb(var(${cssVar}) / <alpha-value>)`

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        canvas: withAlpha('--canvas'),
        surface: withAlpha('--surface'),
        border: withAlpha('--border'),
        ink: {
          DEFAULT: withAlpha('--ink'),
          muted: withAlpha('--ink-muted'),
          faint: withAlpha('--ink-faint'),
        },
        accent: {
          DEFAULT: withAlpha('--accent'),
          hover: withAlpha('--accent-hover'),
          weak: withAlpha('--accent-weak'),
        },
        ok: withAlpha('--ok'),
        warn: withAlpha('--warn'),
        pending: withAlpha('--pending'),
        err: withAlpha('--err'),
      },
      fontFamily: {
        sans: ['"Public Sans Variable"', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono Variable"', 'ui-monospace', 'monospace'],
      },
      borderRadius: {
        control: '6px',
        card: '10px',
      },
      boxShadow: {
        hover: '0 1px 3px rgba(0, 0, 0, 0.06)',
        overlay: '0 10px 40px rgba(0, 0, 0, 0.12)',
      },
      keyframes: {
        shimmer: { '100%': { transform: 'translateX(100%)' } },
        'fade-in': {
          from: { opacity: '0', transform: 'translateY(4px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
      },
      animation: {
        shimmer: 'shimmer 1.4s infinite',
        'fade-in': 'fade-in 0.2s ease both',
      },
    },
  },
  plugins: [],
}
