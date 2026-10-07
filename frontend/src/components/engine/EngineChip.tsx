import { cls } from "../ui";

// Complete static class strings: Tailwind's JIT never sees an interpolated colour.
const LOOK: Record<string, { label: string; tone: string }> = {
  three: { label: "three.js", tone: "text-brand border-brand/40 bg-brand/10" },
  playcanvas: { label: "PlayCanvas", tone: "text-accent border-accent/40 bg-accent/10" },
  babylon: { label: "Babylon", tone: "text-warn border-warn/40 bg-warn/10" },
  phaser: { label: "Phaser", tone: "text-ok border-ok/40 bg-ok/10" },
  pixi: { label: "Pixi", tone: "text-ok border-ok/40 bg-ok/10" },
};

export const engineLabel = (kind: string) => LOOK[kind]?.label || kind || "";

export function EngineChip({ kind, size = "sm", className }: { kind: string; size?: "xs" | "sm"; className?: string }) {
  if (!kind) return null;
  const l = LOOK[kind] || { label: kind, tone: "text-muted border-line bg-panel2" };
  return (
    <span className={cls("inline-flex items-center rounded-full border font-medium whitespace-nowrap",
      size === "xs" ? "px-1.5 text-[9px] leading-4" : "px-2 py-0.5 text-[10px]", l.tone, className)}>
      {l.label}
    </span>
  );
}
