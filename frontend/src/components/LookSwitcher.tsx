import { Palette, SquareTerminal } from "lucide-react";
import { SKINS } from "../theme";
import { useStore } from "../store/useStore";
import { cls } from "./ui";

// Two ways to draw the same studio.
//
// "Studio" is this app's own look. "Claude Code CLI" is the console look: the Windows
// console palette, one monospace face, square edges, and the conversation printed with
// the marks the CLI prints — ⏺ for an action, ⎿ for what it produced, > for your prompt.
//
// It is a skin, not a session. The project, the transcript, the model, the permission
// mode and every key you press are the same in both, so you can switch in the middle of
// a turn and nothing is lost — the very next token still lands in the same chat.
//
// Two shapes, one control:
//   <LookSwitcher rail />   under the pane list, bottom-left of Settings
//   <LookSwitcher />        in Appearance, with the explanation
export function LookSwitcher({ rail }: { rail?: boolean }) {
  const skin = useStore((s) => s.skin);
  const setSkin = useStore((s) => s.setSkin);
  return (
    <div className={cls(rail && "pt-3 mt-3 border-t border-line")}>
      <div className={cls("text-[10px] uppercase tracking-wide text-muted/70 mb-1.5", rail && "px-3")}>
        Look
      </div>
      <div className={cls("grid grid-cols-2 gap-1", rail ? "px-1.5" : "max-w-md")}>
        {SKINS.map((s) => {
          const Icon = s.id === "cli" ? SquareTerminal : Palette;
          const on = skin === s.id;
          return (
            <button key={s.id} onClick={() => setSkin(s.id)} title={s.hint}
              aria-pressed={on}
              className={cls("rounded-lg border px-2 py-2 text-left transition-colors",
                on ? "border-brand/60 bg-brand/10 text-text"
                   : "border-line text-muted hover:text-text hover:bg-panel2/60")}>
              <Icon size={14} className={cls("mb-1", on && "text-brand")} />
              <div className="text-[11px] font-medium leading-tight">{s.label}</div>
              {!rail && <div className="text-[10px] text-muted mt-0.5 leading-snug">{s.hint}</div>}
            </button>
          );
        })}
      </div>
    </div>
  );
}
