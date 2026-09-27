import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { AlertTriangle, CheckCircle2, Info, Loader2, RefreshCw, X, XCircle } from "lucide-react";

// ------------------------------------------------------------------ toasts

type Toast = { id: number; text: string; kind: "ok" | "err" };
const ToastCtx = createContext<(text: string, kind?: "ok" | "err") => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const dismiss = useCallback((id: number) => setToasts((t) => t.filter((x) => x.id !== id)), []);
  const push = useCallback(
    (text: string, kind: "ok" | "err" = "ok") => {
      const id = Date.now() + Math.random();
      setToasts((t) => [...t, { id, text, kind }]);
      window.setTimeout(() => dismiss(id), kind === "err" ? 7000 : 3500);
    },
    [dismiss],
  );
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.kind === "err" ? "err" : ""}`}>
            {t.kind === "err" ? <XCircle size={16} className="icon" /> : <CheckCircle2 size={16} className="icon" />}
            <span className="msg">{t.text}</span>
            <button type="button" className="close" aria-label="Dismiss" onClick={() => dismiss(t.id)}>
              <X size={14} />
            </button>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export const useToast = () => useContext(ToastCtx);

// ------------------------------------------------------------------ callouts

const CALLOUT_ICON = { err: XCircle, warn: AlertTriangle, ok: CheckCircle2, info: Info };
const CALLOUT_COLOR = { err: "var(--err)", warn: "var(--warn)", ok: "var(--ok)", info: "var(--info)" };

export function Callout({ kind = "info", title, children }: { kind?: "info" | "warn" | "err" | "ok"; title?: ReactNode; children?: ReactNode }) {
  const Icon = CALLOUT_ICON[kind];
  return (
    <div className={`callout ${kind}`}>
      <Icon size={16} className="icon" color={CALLOUT_COLOR[kind]} aria-hidden />
      <div className="min0 flex-1">
        {title && <div className="title">{title}</div>}
        {children && <div className="text">{children}</div>}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ empty / loading / error

export function Empty({ title, children, action, icon, compact }: { title: string; children?: ReactNode; action?: ReactNode; icon?: ReactNode; compact?: boolean }) {
  return (
    <div className={`empty ${compact ? "compact" : ""}`}>
      {icon && <div className="empty-icon">{icon}</div>}
      <h3>{title}</h3>
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}

export function Skeleton({ h = 16, w = "100%", r }: { h?: number; w?: number | string; r?: number }) {
  return <span className="skeleton" style={{ height: h, width: w, borderRadius: r }} aria-hidden />;
}

/** Placeholder while a page's first data loads. */
export function LoadingState({ label = "Loading…", rows = 3 }: { label?: string; rows?: number }) {
  return (
    <div className="skeleton-page" role="status" aria-label={label}>
      <Skeleton h={28} w={240} />
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} h={i === 0 ? 120 : 72} r={10} />
      ))}
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <div className="state-box" role="status">
      <Loader2 size={20} className="spin" aria-hidden />
      {label && <p>{label}</p>}
    </div>
  );
}

export function ErrorState({ title = "Something went wrong", error, onRetry }: { title?: string; error?: unknown; onRetry?: () => void }) {
  const message = error instanceof Error ? error.message : typeof error === "string" ? error : null;
  return (
    <div className="state-box" role="alert">
      <div className="empty-icon err">
        <XCircle size={20} />
      </div>
      <h3>{title}</h3>
      {message && <p>{message}</p>}
      {onRetry && (
        <button className="btn" onClick={onRetry}>
          <RefreshCw size={14} /> Try again
        </button>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ copy block

export function CopyBlock({ text, label = "Copy" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="copy-block">
      <pre className="log">{text}</pre>
      <button
        className="btn sm"
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(text);
          } catch {
            const ta = document.createElement("textarea");
            ta.value = text;
            document.body.appendChild(ta);
            ta.select();
            document.execCommand("copy");
            ta.remove();
          }
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1500);
        }}
      >
        {copied ? "Copied" : label}
      </button>
    </div>
  );
}
