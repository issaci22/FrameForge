import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { ArrowLeft } from "lucide-react";

/** The FrameForge mark (three amber bars, fading like frames) and wordmark. */
export function Logo({ size }: { size?: "lg" }) {
  return (
    <span className={`logo ${size ?? ""}`}>
      <span className="brand-mark" aria-hidden>
        <span />
        <span />
        <span />
      </span>
      FrameForge
    </span>
  );
}

export function PageHeader({
  title,
  description,
  actions,
  back,
  prefix,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  back?: { to: string; label: string };
  prefix?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div className="titles">
        {back && (
          <Link to={back.to} className="back">
            <ArrowLeft size={14} /> {back.label}
          </Link>
        )}
        <h1>
          {prefix}
          <span className="ttl">{title}</span>
        </h1>
        {description && <div className="desc">{description}</div>}
      </div>
      {actions && <div className="actions">{actions}</div>}
    </header>
  );
}

/** One titled surface on a page. `flush` removes the body padding for tables and lists. */
export function Section({
  title,
  description,
  actions,
  children,
  flush,
  className,
  id,
}: {
  title?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  flush?: boolean;
  className?: string;
  id?: string;
}) {
  return (
    <section className={`panel ${className ?? ""}`} id={id}>
      {(title || actions) && (
        <div className="panel-head">
          <div className="titles">
            {title && <h2>{title}</h2>}
            {description && <div className="panel-desc">{description}</div>}
          </div>
          {actions && <div className="right">{actions}</div>}
        </div>
      )}
      {children != null && children !== false && <div className={`panel-body ${flush ? "tight" : ""}`}>{children}</div>}
    </section>
  );
}

export function Tabs<T extends string>({
  value,
  onChange,
  tabs,
  variant,
}: {
  value: T;
  onChange: (v: T) => void;
  tabs: { value: T; label: ReactNode; count?: number }[];
  variant?: "pills";
}) {
  return (
    <div className={`tabs ${variant ?? ""}`} role="tablist">
      {tabs.map((t) => (
        <button key={t.value} type="button" role="tab" aria-selected={t.value === value} className={t.value === value ? "on" : ""} onClick={() => onChange(t.value)}>
          {t.label}
          {t.count != null && <span className="n">{t.count}</span>}
        </button>
      ))}
    </div>
  );
}
