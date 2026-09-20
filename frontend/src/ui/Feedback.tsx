import { Alert, Button, Empty, Spin } from "antd";
import type { ReactNode } from "react";

export function ErrorNotice({
  message,
  onRetry,
  retryLabel = "重新加载",
  className = "",
}: {
  message: string;
  onRetry?: () => void;
  retryLabel?: string;
  className?: string;
}) {
  return (
    <Alert
      className={`ui-feedback ${className}`}
      type="error"
      showIcon
      title={message}
      action={
        onRetry && (
          <Button size="small" onClick={onRetry}>
            {retryLabel}
          </Button>
        )
      }
    />
  );
}

export function LoadingState({ label = "正在加载…" }: { label?: string }) {
  return (
    <div className="ui-loading" role="status">
      <Spin size="small" />
      <span>{label}</span>
    </div>
  );
}

export function EmptyState({
  title,
  description,
}: {
  title: string;
  description?: ReactNode;
}) {
  return (
    <div className="ui-empty">
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={
          <>
            <strong>{title}</strong>
            {description && <p>{description}</p>}
          </>
        }
      />
    </div>
  );
}
