import type { ReactNode } from "react";

export function Field({ label, help, error, children, className, htmlFor }: { label?: ReactNode; help?: ReactNode; error?: string | null; children: ReactNode; className?: string; htmlFor?: string }) {
  return (
    <div className={`field ${className ?? ""}`}>
      {label && <label htmlFor={htmlFor}>{label}</label>}
      {children}
      {help && !error && <div className="help">{help}</div>}
      {error && <div className="error-text">{error}</div>}
    </div>
  );
}

/** Label and explanation on the left, the control on the right. Stacks on narrow screens. */
export function SettingRow({ label, description, children, top, dim }: { label: ReactNode; description?: ReactNode; children: ReactNode; top?: boolean; dim?: boolean }) {
  return (
    <div className={`setting-row ${top ? "top" : ""} ${dim ? "dim" : ""}`}>
      <div className="label">
        <b>{label}</b>
        {description && <span>{description}</span>}
      </div>
      <div className="control">{children}</div>
    </div>
  );
}

export function Toggle({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label?: ReactNode; disabled?: boolean }) {
  return (
    <label className={`toggle ${disabled ? "disabled" : ""}`}>
      <input type="checkbox" role="switch" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
      <span className="track" aria-hidden />
      {label && <span>{label}</span>}
    </label>
  );
}

export function Segmented<T extends string | number | null>({
  value,
  options,
  onChange,
}: {
  value: T;
  options: { value: T; label: ReactNode; disabled?: boolean; title?: string }[];
  onChange: (v: T) => void;
}) {
  return (
    <div className="segmented" role="radiogroup">
      {options.map((o) => (
        <button
          key={String(o.value)}
          type="button"
          role="radio"
          aria-checked={o.value === value}
          className={o.value === value ? "on" : ""}
          disabled={o.disabled}
          title={o.title}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function NumberInput({
  value,
  onChange,
  suffix,
  min,
  max,
  step,
  placeholder,
  allowEmpty,
}: {
  value: number | null;
  onChange: (v: number | null) => void;
  suffix?: string;
  min?: number;
  max?: number;
  step?: number;
  placeholder?: string;
  allowEmpty?: boolean;
}) {
  const input = (
    <input
      className="input mono"
      type="number"
      value={value ?? ""}
      min={min}
      max={max}
      step={step}
      placeholder={placeholder}
      onChange={(e) => {
        const raw = e.target.value;
        if (raw === "") return onChange(allowEmpty ? null : (min ?? 0));
        const n = Number(raw);
        if (!Number.isNaN(n)) onChange(n);
      }}
    />
  );
  return suffix ? (
    <div className="input-suffix">
      {input}
      <span>{suffix}</span>
    </div>
  ) : (
    input
  );
}
