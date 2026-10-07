/** @type {import('tailwindcss').Config} */
// Colors are driven by CSS variables (RGB channel triplets) so themes can be
// swapped at runtime via [data-theme] on <html>. Alpha utilities (bg-panel/50)
// keep working because we use the `rgb(var(--x) / <alpha-value>)` form.
const c = (v) => `rgb(var(${v}) / <alpha-value>)`;

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: c("--c-bg"),
        panel: c("--c-panel"),
        panel2: c("--c-panel2"),
        line: c("--c-line"),
        muted: c("--c-muted"),
        text: c("--c-text"),
        brand: { DEFAULT: c("--c-brand"), 600: c("--c-brand600"), 700: c("--c-brand700") },
        accent: c("--c-accent"),
        ok: c("--c-ok"),
        warn: c("--c-warn"),
        danger: c("--c-danger"),
      },
      fontFamily: {
        sans: ["Inter", "Segoe UI", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "Consolas", "monospace"],
      },
      boxShadow: {
        card: "0 1px 0 rgba(255,255,255,0.03) inset, 0 8px 24px rgba(0,0,0,0.35)",
      },
    },
  },
  plugins: [],
};
