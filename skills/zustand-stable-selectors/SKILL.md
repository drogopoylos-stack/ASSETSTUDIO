---
name: zustand-stable-selectors
description: Use when a React + zustand (or any useSyncExternalStore) app renders a blank/white screen or throws "Maximum update depth"/"getSnapshot should be cached" after adding a store selector — the selector returns a fresh reference each call.
metadata:
  category: ui-ux
  updated: 2026-06-19
  confidence: verified
  source: experience
disable-model-invocation: true
---

# zustand selectors must return a stable reference

## The footgun
A zustand selector runs on **every** store change and React compares the result with
`Object.is`. If the selector **creates a new value each call**, the comparison is always
false → re-render → store re-reads → **infinite loop**. With React 18's
`useSyncExternalStore` (zustand v4+) this becomes a hard crash:
*"The result of getSnapshot should be cached"* / *"Maximum update depth exceeded"* — which
usually shows as a **blank white screen**, the error only in the devtools console.

The classic triggers — each returns a NEW reference every call:
```ts
const items = useStore((s) => s.itemsById[id] || []);        // fresh [] when key missing
const list  = useStore((s) => s.things.filter((x) => x.on)); // fresh array every call
const obj   = useStore((s) => ({ a: s.a, b: s.b }));         // fresh object every call
```

## Fixes
**1. Hoist a stable fallback to module scope** (fixes the `|| []` / `|| {}` case):
```ts
const EMPTY: Item[] = [];                          // ONE identity for the whole app
const items = useStore((s) => s.itemsById[id] || EMPTY);
```
**2. Select raw, derive outside the selector:**
```ts
const things = useStore((s) => s.things);          // stable slice
const list = useMemo(() => things.filter((x) => x.on), [things]);
```
**3. Select primitives, or use `useShallow` for object/array results:**
```ts
import { useShallow } from "zustand/react/shallow";
const { a, b } = useStore(useShallow((s) => ({ a: s.a, b: s.b })));
```

## Rules of thumb
- A selector must be **pure and reference-stable**: same state ⇒ same `===` result.
- Never `|| []`, `|| {}`, `.map`, `.filter`, `.slice`, or an object/array literal **inside** a
  selector — hoist the empty constant or move the derivation into `useMemo`.
- **Symptom → cause map:** blank screen + console "getSnapshot should be cached" or
  "Maximum update depth" ⇒ inspect the **most recently added selector** first; it's almost
  always a freshly-allocated array/object.
- Same rule applies to any `useSyncExternalStore`-based store and to Redux's `useSelector`
  (which warns instead of looping) — return stable refs or supply a shallow-equality fn.
