import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";
import { Info, MoreHorizontal, X } from "lucide-react";

// ------------------------------------------------------------------ escape stack

/**
 * Escape closes only the top-most overlay. A confirm popover inside a modal, or a folder picker over a library
 * editor, closes by itself instead of taking its parent with it.
 */
const escapeStack: (() => void)[] = [];
let escapeListening = false;

function onEscapeKey(e: KeyboardEvent) {
  if (e.key !== "Escape" || !escapeStack.length) return;
  e.preventDefault();
  escapeStack[escapeStack.length - 1]();
}

export function useEscape(onClose: () => void, active = true) {
  const ref = useRef(onClose);
  ref.current = onClose;
  useEffect(() => {
    if (!active) return;
    const fn = () => ref.current();
    escapeStack.push(fn);
    if (!escapeListening) {
      window.addEventListener("keydown", onEscapeKey);
      escapeListening = true;
    }
    return () => {
      const i = escapeStack.lastIndexOf(fn);
      if (i >= 0) escapeStack.splice(i, 1);
    };
  }, [active]);
}

/** Move focus into a dialog when it opens and give it back when it closes. */
function useDialogFocus(ref: RefObject<HTMLElement | null>) {
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const el = ref.current;
    if (el && !el.contains(document.activeElement)) el.focus({ preventScroll: true });
    return () => {
      if (previous && document.contains(previous)) previous.focus({ preventScroll: true });
    };
  }, [ref]);
}

// ------------------------------------------------------------------ modal / drawer

export function Modal({
  title,
  subtitle,
  onClose,
  children,
  footer,
  wide,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useEscape(onClose);
  useDialogFocus(ref);
  return createPortal(
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={ref} className={`modal ${wide ? "wide" : ""}`} role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1}>
        <div className="modal-head">
          <div className="flex-1">
            <h2 id={titleId}>{title}</h2>
            {subtitle && <div className="sub">{subtitle}</div>}
          </div>
          <button className="btn ghost icon" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}

export function Drawer({ title, subtitle, onClose, children, actions }: { title: ReactNode; subtitle?: ReactNode; onClose: () => void; children: ReactNode; actions?: ReactNode }) {
  const ref = useRef<HTMLElement>(null);
  const titleId = useId();
  useEscape(onClose);
  useDialogFocus(ref);
  return createPortal(
    <>
      <div className="drawer-overlay" onClick={onClose} />
      <aside ref={ref} className="drawer" role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1}>
        <div className="drawer-head">
          <div className="flex-1">
            <h2 id={titleId} className="ellipsis">
              {title}
            </h2>
            {subtitle && <div className="sub">{subtitle}</div>}
          </div>
          {actions}
          <button className="btn ghost icon" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </div>
        <div className="drawer-body">{children}</div>
      </aside>
    </>,
    document.body,
  );
}

// ------------------------------------------------------------------ floating panels

type Align = "start" | "end";

/** Position a fixed panel under (or, without room, above) its anchor, kept inside the viewport. */
function useFloating(open: boolean, anchor: RefObject<HTMLElement | null>, panel: RefObject<HTMLElement | null>, align: Align, content?: unknown) {
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  useLayoutEffect(() => {
    if (!open) {
      setPos(null);
      return;
    }
    const place = () => {
      const a = anchor.current?.getBoundingClientRect();
      const p = panel.current;
      if (!a || !p) return;
      const w = p.offsetWidth;
      const h = p.offsetHeight;
      let left = align === "end" ? a.right - w : a.left;
      left = Math.max(8, Math.min(left, window.innerWidth - w - 8));
      let top = a.bottom + 6;
      if (top + h > window.innerHeight - 8 && a.top - h - 6 > 8) top = a.top - h - 6;
      setPos({ top, left });
    };
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
    // `content` changes when the panel swaps what it shows (a menu turning into a confirmation), so re-measure.
  }, [open, anchor, panel, align, content]);
  return pos;
}

function useOutsideClose(open: boolean, refs: RefObject<HTMLElement | null>[], onClose: () => void) {
  const cb = useRef(onClose);
  cb.current = onClose;
  useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => {
      if (refs.some((r) => r.current?.contains(e.target as Node))) return;
      cb.current();
    };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
}

function floatStyle(pos: { top: number; left: number } | null) {
  // Until it is measured the panel is transparent, not hidden, so focus can already move into it.
  return pos ? { top: pos.top, left: pos.left } : { top: 0, left: 0, opacity: 0, pointerEvents: "none" as const };
}

function ConfirmPanel({ text, detail, label, danger, onCancel, onConfirm }: { text: ReactNode; detail?: ReactNode; label: string; danger: boolean; onCancel: () => void; onConfirm: () => void }) {
  const confirmRef = useRef<HTMLButtonElement>(null);
  useEffect(() => confirmRef.current?.focus({ preventScroll: true }), []);
  return (
    <>
      <div>
        <div className="q">{text}</div>
        {detail && <div className="d">{detail}</div>}
      </div>
      <div className="actions">
        <button type="button" className="btn sm" onClick={onCancel}>
          Cancel
        </button>
        <button type="button" ref={confirmRef} className={`btn sm ${danger ? "danger-solid" : "primary"}`} onClick={onConfirm}>
          {label}
        </button>
      </div>
    </>
  );
}

/**
 * A button for consequential actions. The first click opens a small confirmation next to it; nothing happens
 * until that is confirmed.
 */
export function ConfirmButton({
  onConfirm,
  children,
  confirmText = "Are you sure?",
  detail,
  confirmLabel,
  className = "btn danger sm",
  disabled,
  title,
}: {
  onConfirm: () => void;
  children: ReactNode;
  confirmText?: ReactNode;
  detail?: ReactNode;
  confirmLabel?: string;
  className?: string;
  disabled?: boolean;
  title?: string;
}) {
  const [open, setOpen] = useState(false);
  const anchor = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const pos = useFloating(open, anchor, panel, "end");
  const close = () => {
    setOpen(false);
    anchor.current?.focus({ preventScroll: true });
  };
  useEscape(close, open);
  useOutsideClose(open, [anchor, panel], () => setOpen(false));
  const danger = className.includes("danger");

  return (
    <>
      <button
        ref={anchor}
        type="button"
        className={`${className} ${open ? "armed" : ""}`}
        disabled={disabled}
        title={title}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={(e) => {
          e.stopPropagation();
          setOpen(!open);
        }}
      >
        {children}
      </button>
      {open &&
        createPortal(
          <div ref={panel} className="popover" role="alertdialog" style={floatStyle(pos)} onClick={(e) => e.stopPropagation()}>
            <ConfirmPanel
              text={confirmText}
              detail={detail}
              label={confirmLabel ?? (danger ? "Confirm" : "Continue")}
              danger={danger}
              onCancel={close}
              onConfirm={() => {
                setOpen(false);
                onConfirm();
              }}
            />
          </div>,
          document.body,
        )}
    </>
  );
}

// ------------------------------------------------------------------ menu

export interface MenuItem {
  label: ReactNode;
  icon?: ReactNode;
  onSelect: () => void;
  danger?: boolean;
  disabled?: boolean;
  /** Ask before running: the menu turns into a confirmation. */
  confirm?: { text: ReactNode; detail?: ReactNode; label?: string };
}

/** Overflow menu ("⋯") for secondary and destructive actions, so they don't crowd the resting view. */
export function Menu({ items, label = "More actions", trigger, className = "btn ghost icon sm" }: { items: (MenuItem | "sep" | null | false)[]; label?: string; trigger?: ReactNode; className?: string }) {
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState<MenuItem | null>(null);
  const anchor = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const pos = useFloating(open, anchor, panel, "end", confirming);
  const shown = items.filter(Boolean) as (MenuItem | "sep")[];

  const close = (refocus = true) => {
    setOpen(false);
    setConfirming(null);
    if (refocus) anchor.current?.focus({ preventScroll: true });
  };
  useEscape(() => close(), open);
  useOutsideClose(open, [anchor, panel], () => close(false));

  useEffect(() => {
    if (open && !confirming) panel.current?.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus({ preventScroll: true });
  }, [open, confirming]);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (confirming) return;
    const buttons = [...(panel.current?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)") ?? [])];
    const i = buttons.indexOf(document.activeElement as HTMLButtonElement);
    if (e.key === "ArrowDown") {
      e.preventDefault();
      buttons[(i + 1) % buttons.length]?.focus();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      buttons[(i - 1 + buttons.length) % buttons.length]?.focus();
    } else if (e.key === "Home") {
      e.preventDefault();
      buttons[0]?.focus();
    } else if (e.key === "End") {
      e.preventDefault();
      buttons[buttons.length - 1]?.focus();
    } else if (e.key === "Tab") {
      close(false);
    }
  };

  return (
    <>
      <button
        ref={anchor}
        type="button"
        className={className}
        aria-label={label}
        title={label}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={(e) => {
          e.stopPropagation();
          if (open) close(false);
          else setOpen(true);
        }}
      >
        {trigger ?? <MoreHorizontal size={16} />}
      </button>
      {open &&
        createPortal(
          <div ref={panel} className={confirming ? "popover" : "menu"} role={confirming ? "alertdialog" : "menu"} style={floatStyle(pos)} onKeyDown={onKeyDown} onClick={(e) => e.stopPropagation()}>
            {confirming ? (
              <ConfirmPanel
                text={confirming.confirm!.text}
                detail={confirming.confirm!.detail}
                label={confirming.confirm!.label ?? "Confirm"}
                danger={!!confirming.danger}
                onCancel={() => close()}
                onConfirm={() => {
                  const run = confirming.onSelect;
                  close(false);
                  run();
                }}
              />
            ) : (
              shown.map((it, i) =>
                it === "sep" ? (
                  <div key={`sep-${i}`} className="sep" role="separator" />
                ) : (
                  <button
                    key={i}
                    type="button"
                    role="menuitem"
                    className={it.danger ? "danger" : ""}
                    disabled={it.disabled}
                    onClick={() => {
                      if (it.confirm) return setConfirming(it);
                      close(false);
                      it.onSelect();
                    }}
                  >
                    {it.icon}
                    {it.label}
                  </button>
                ),
              )
            )}
          </div>,
          document.body,
        )}
    </>
  );
}

// ------------------------------------------------------------------ tooltip

/** Hover/focus tooltip. Use for supplementary explanations; anything essential belongs on the page. */
export function Tooltip({ text, children, className }: { text: ReactNode; children: ReactNode; className?: string }) {
  const [open, setOpen] = useState(false);
  const anchor = useRef<HTMLSpanElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const pos = useFloating(open, anchor, panel, "start");
  const id = useId();
  return (
    <>
      <span
        ref={anchor}
        className={className ?? "tip-anchor"}
        aria-describedby={open ? id : undefined}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
      >
        {children}
      </span>
      {open &&
        createPortal(
          <div ref={panel} id={id} role="tooltip" className="tip" style={floatStyle(pos)}>
            {text}
          </div>,
          document.body,
        )}
    </>
  );
}

/** A small (i) that explains a setting on hover or focus. */
export function InfoTip({ text }: { text: ReactNode }) {
  return (
    <Tooltip text={text} className="tip-anchor">
      <button type="button" className="info-tip" aria-label={typeof text === "string" ? text : "More information"}>
        <Info size={14} />
      </button>
    </Tooltip>
  );
}
