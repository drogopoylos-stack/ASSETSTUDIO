import { CheckCircle2, Info, X, AlertTriangle, XCircle } from "lucide-react";
import { useStore } from "../store/useStore";
import { cls } from "./ui";

const ICON = { info: Info, ok: CheckCircle2, warn: AlertTriangle, danger: XCircle };
const COLOR = { info: "text-brand", ok: "text-ok", warn: "text-warn", danger: "text-danger" };

export default function Toasts() {
  const { toasts, dismissToast } = useStore();
  return (
    <div className="fixed bottom-12 right-4 z-50 flex flex-col gap-2 w-80">
      {toasts.map((t) => {
        const Icon = ICON[t.kind];
        return (
          <div key={t.id} className="card p-3 flex items-start gap-2 animate-in">
            <Icon size={16} className={cls("mt-0.5 shrink-0", COLOR[t.kind])} />
            <span className="text-sm flex-1">{t.message}</span>
            <button onClick={() => dismissToast(t.id)} className="text-muted hover:text-text">
              <X size={14} />
            </button>
          </div>
        );
      })}
    </div>
  );
}
