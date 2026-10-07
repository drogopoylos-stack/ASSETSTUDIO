---
name: tailwind-play-cdn-gotchas
description: Use when styling a page with the Tailwind Play CDN (cdn.tailwindcss.com) — invalid-class traps that silently break @apply, config, gradients, and runtime-injected classes.
disable-model-invocation: true
metadata:
  category: ui-ux
  created: 2026-06-20
  updated: 2026-06-20
  confidence: verified
  source: experience (debugged a live CDN page, 2026-06)
---

# Tailwind Play CDN — gotchas that silently break styling

For prototypes using `<script src="https://cdn.tailwindcss.com"></script>` (dev-only; not for production).

## Numeric font-weights are NOT classes
`font-600` / `font-700` are **not** Tailwind utilities — they do nothing. Use `font-medium` (500), `font-semibold` (600), `font-bold` (700), or arbitrary `font-[600]`.

## One invalid utility in `@apply` kills the WHOLE custom block
Inside `<style type="text/tailwindcss"> … </style>`, an `@apply` with a non-existent class (e.g. `@apply font-700`) throws at compile time and **the entire block fails to compile** — every `.btn`, `.nav-item`, etc. in that block renders unstyled. Symptom: a pile of components suddenly look default/broken after adding one rule. Fix: only `@apply` real utilities; if components lost styling, search the block for an invalid class.

## Config goes in a script right after the CDN tag
```html
<script src="https://cdn.tailwindcss.com"></script>
<script>
  tailwind.config = { darkMode: 'class',
    theme: { extend: { colors: { brand: { 600: '#159FC9' } }, fontFamily: { sans: ['DM Sans','sans-serif'] } } } };
</script>
```

## Arbitrary values: fine in HTML, fragile in @apply
`class="bg-gradient-to-r from-[#0E9E97] via-[#159FC9] to-[#2A66C2]"` works in markup. For the same gradient inside a component, prefer a **plain CSS rule** over `@apply` with arbitrary color stops:
```css
.btn-primary { background-image: linear-gradient(110deg, #0FA39A 0%, #1AAFD0 52%, #2C72C6 100%); }
```

## Runtime-injected classes ARE picked up — but only whole tokens
The Play CDN runs a MutationObserver, so classes added later via `innerHTML` (e.g. building a table row with `bg-emerald-100`) do get generated. Caveat: only **complete literal class strings** are detected — never assemble class names from interpolated fragments (`` `bg-${c}-100` `` may not generate). Write the full class literally.
