import type { ReactNode } from "react";

export function WorkspaceHeading({
  step,
  title,
  description,
  children,
}: {
  step: string;
  title: string;
  description: string;
  children?: ReactNode;
}) {
  return (
    <div className="page-heading">
      <div>
        <p className="eyebrow">DATA WORKSPACE / STEP {step}</p>
        <h1>{title}</h1>
        <p className="description">{description}</p>
      </div>
      {children}
    </div>
  );
}
