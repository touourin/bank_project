import type { ReactNode } from "react";
import { Modal } from "antd";
import { useAsyncAction } from "../hooks/useAsyncAction";
import { ErrorNotice } from "./Feedback";

/** Mount for one pending action. Failed actions stay open and can be retried. */
export function ConfirmDialog({
  title,
  children,
  onConfirm,
  onClose,
  confirmLabel = "确认",
  danger = false,
  disabled = false,
}: {
  title: string;
  children: ReactNode;
  onConfirm: () => Promise<void>;
  onClose: () => void;
  confirmLabel?: string;
  danger?: boolean;
  disabled?: boolean;
}) {
  const { busy, error, execute } = useAsyncAction();
  async function confirm() {
    if (!disabled) await execute("confirm", onConfirm, { onSuccess: onClose });
  }
  return (
    <Modal
      open
      title={title}
      centered
      okText={confirmLabel}
      cancelText="取消"
      okButtonProps={{
        danger,
        disabled,
        "aria-label": confirmLabel,
        "aria-busy": busy,
      }}
      confirmLoading={busy}
      cancelButtonProps={{ disabled: busy }}
      closable={busy ? false : { "aria-label": "关闭" }}
      keyboard={!busy}
      mask={{ closable: !busy }}
      onCancel={() => {
        if (!busy) onClose();
      }}
      onOk={confirm}
    >
      <div className="ui-confirm-content">{children}</div>
      {error && <ErrorNotice message={error} />}
    </Modal>
  );
}
