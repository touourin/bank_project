import { useRef, useState, type ReactNode } from "react";
import { Modal } from "antd";
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
  const inFlight = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function confirm() {
    if (inFlight.current || disabled) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    try {
      await onConfirm();
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "操作失败，请重试");
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
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
        if (!inFlight.current) onClose();
      }}
      onOk={confirm}
    >
      <div className="ui-confirm-content">{children}</div>
      {error && <ErrorNotice message={error} />}
    </Modal>
  );
}
