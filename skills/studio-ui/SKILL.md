---
name: studio-ui
description: Validated UI/UX patterns, component recipes, and gotchas accumulated across projects (React/Tailwind, layout, theming, accessibility). Auto-captured when Auto-learn is on.
metadata:
  category: ui-ux
  updated: 2026-06-19
  auto: true
disable-model-invocation: true
---

# Studio · UI / UX

Validated UI/UX learnings — each a dated, sourced `## entry` you can edit or remove.
Auto-captured when **Auto-learn** (the 🎓 toggle in the chat box) is on.

<!-- learnings are appended below -->

## Optimistic "sent/queued" chat bubbles + mid-turn steering tag (2026-06-19)

**Goal:** a message (esp. a 2nd one sent while the agent is busy) must visibly land in the
chat immediately, and a mid-work message should be treated as a correction, not buried.

**Optimistic bubble (confirmation):** keep just-sent messages in a per-project store
(`inflight[projectId]`), and in the transcript feed render any inflight item whose text has
NOT yet appeared as a real user event — deduped on `firstLine(stripTag(text))`. The optimistic
bubble (spinner + "sending…/queued · picked up next") is auto-replaced by the real transcript
bubble the moment it echoes back. Add `inflight` to the auto-scroll-pin deps so the new bubble
sticks to the bottom. Gotcha: a fresh-session feed that early-returns (no transcript load) must
still render pending bubbles, else the first message shows no confirmation.

**Mid-turn steering (correction):** in a persistent stream-json session, a message written while
a turn is active is queued and consumed at the NEXT turn boundary (not mid-tool-call). Detect
"busy at write time" (`turn_active or last_write > last_result`) and prepend a strippable marker
line (`STEER_PREFIX`, byte-identical on both ends) telling the agent: *if this corrects/refines
the previous request, adjust course and prioritize it.* The frontend strips the marker from the
visible bubble (slice after first `\n\n`) and shows a small "↪ steering · folded into the work"
badge. So the agent gets the steer; the user's transcript stays clean. For an *immediate* hard
re-plan (not next-boundary), the Stop/interrupt button is the only real option — be honest about
that. **Source:** STUDIO `SessionFeed.tsx` (PendingRow/stripSteer), `cc_session._steer_wrap`.

## A `@media` override must come AFTER the base rule (2026-06-17)
Media queries add **zero specificity**. When a responsive override and the base rule have equal specificity (e.g. both `.menu-buttons`), the cascade is decided by **source order** — so an `@media` block placed *before* the base rule LOSES even when it matches, and the override silently does nothing. Symptom: a landscape/mobile layout that "won't apply" despite the query matching. Fix: put the `@media` block physically *after* the base declaration (or raise specificity). Verified by a layout that stayed `display:flex` because the landscape `display:grid` override sat earlier in the stylesheet.

## Short-landscape phone menu that fits without scroll (2026-06-17)
Centered, non-scrollable full-screen flex columns (and `pointer-events:none` overlays that can't be touch-scrolled) cut off their bottom controls on short landscape phones (~300–360px tall). Recipe: gate on `@media (orientation: landscape) and (max-height: 480px)`, switch a stacked button column to a **2-column grid** (`grid-template-columns:1fr 1fr`; let the primary CTA `grid-column:1/-1` span the top row), shrink logo/padding, and **override any portrait `padding-top`** (a portrait "clear the header" rule like `padding-top:100px` is catastrophic in a 320px-tall view). Halving the button-block height frees room so everything fits — no scroll needed.

## Spectrum swatch for a hue-cycling cosmetic (2026-06-17)
A "rainbow"/hue-cycling cosmetic usually stores a single base color (e.g. `#ff5050`) and only animates the spectrum at runtime in the render loop — so any static UI swatch/thumbnail drawn from that base color looks like one flat color (red). Fix: special-case that item id in the thumbnail renderer and paint a CSS `linear-gradient(90deg, …)` across the spectrum instead of the flat color, so the swatch previews what the cosmetic actually does.
