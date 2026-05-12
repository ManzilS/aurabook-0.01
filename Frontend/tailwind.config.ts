import type { Config } from 'tailwindcss'

const config: Config = {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        night: '#0a0a0a',
        surface: '#111111',
        border: '#1f1f1f',
        text: {
          primary: '#ffffff',
          secondary: '#888888',
        },
        accent: {
          DEFAULT: '#00d4ff',
          soft: 'rgba(0, 212, 255, 0.14)',
        },
        success: '#00ff88',
        warning: '#ff6b35',
        danger: '#ff3333',
      },
      boxShadow: {
        glow: '0 0 0 1px rgba(0, 212, 255, 0.2), 0 14px 40px rgba(0, 212, 255, 0.15)',
        panel: '0 20px 80px rgba(0, 0, 0, 0.45)',
      },
      fontFamily: {
        sans: ['Inter', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'monospace'],
      },
      backgroundImage: {
        'hero-radial':
          'radial-gradient(circle at top left, rgba(0, 212, 255, 0.16), transparent 38%), radial-gradient(circle at 80% 20%, rgba(0, 255, 136, 0.16), transparent 28%), radial-gradient(circle at bottom right, rgba(255, 107, 53, 0.16), transparent 34%)',
      },
      animation: {
        float: 'float 16s ease-in-out infinite',
        'float-slow': 'float 22s ease-in-out infinite',
        ticker: 'ticker 28s linear infinite',
        pulsegrid: 'pulsegrid 9s ease-in-out infinite',
        'page-in': 'page-in 0.45s ease-out both',
        'feed-in': 'feed-in 0.45s ease-out both',
        shimmer: 'shimmer 1.5s linear infinite',
      },
      keyframes: {
        float: {
          '0%, 100%': { transform: 'translate3d(0, 0, 0) scale(1)' },
          '50%': { transform: 'translate3d(0, -24px, 0) scale(1.04)' },
        },
        ticker: {
          '0%': { transform: 'translateX(0)' },
          '100%': { transform: 'translateX(-50%)' },
        },
        pulsegrid: {
          '0%, 100%': { opacity: '0.5' },
          '50%': { opacity: '0.9' },
        },
        'page-in': {
          from: { opacity: '0', transform: 'translateY(16px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        'feed-in': {
          from: { opacity: '0', transform: 'translateY(-20px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        shimmer: {
          '0%': { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0' },
        },
      },
    },
  },
  plugins: [],
}

export default config
