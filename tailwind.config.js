/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './templates/**/*.html',
    './signup/templates/**/*.html',
    './accounts/templates/**/*.html',
    './web_interface/templates/**/*.html',
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
      colors: {
        primary: {
          50: '#eff6ff',
          500: '#3b82f6',
          600: '#2563eb',
          700: '#1d4ed8',
          900: '#1e3a8a',
        },
        // Warm accent used for restaurants/food-related UI, so
        // "destination" (primary blue) vs "restaurant" (accent)
        // has one consistent meaning across every page, rather
        // than ad-hoc blue-500/orange-500 picks per template.
        accent: {
          50: '#fef3e2',
          400: '#f0a83e',
          500: '#e08e22',
          600: '#c2740f',
          700: '#9c5b0c',
        },
      },
      keyframes: {
        fadeIn: {
          from: { opacity: '0' },
          to: { opacity: '1' },
        },
        slideUp: {
          from: { transform: 'translateY(20px)', opacity: '0' },
          to: { transform: 'translateY(0)', opacity: '1' },
        },
      },
      animation: {
        'fade-in': 'fadeIn 0.5s ease-in-out',
        'slide-up': 'slideUp 0.3s ease-out',
        'pulse-slow': 'pulse 3s infinite',
      },
    },
  },
  plugins: [],
};
