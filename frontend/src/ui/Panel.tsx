import type { ReactNode } from "react";

interface PanelProps {
  title?: ReactNode;
  description?: ReactNode;
  eyebrow?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  padded?: boolean;
}

export function Panel({
  title,
  description,
  eyebrow,
  actions,
  children,
  className = "",
  padded = false,
}: PanelProps) {
  return (
    <section className={`ui-panel ${className}`}>
      {title && (
        <header className="ui-panel-heading">
          <div>
            {eyebrow && <p className="eyebrow">{eyebrow}</p>}
            <h2>{title}</h2>
            {description && (
              <p className="ui-panel-description">{description}</p>
            )}
          </div>
          {actions && <div className="ui-panel-actions">{actions}</div>}
        </header>
      )}
      <div className={padded ? "ui-panel-body padded" : "ui-panel-body"}>
        {children}
      </div>
    </section>
  );
}
