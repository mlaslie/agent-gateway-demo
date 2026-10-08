import { useEffect, useRef, type ReactNode } from "react";

export function ConfirmDialog(props: {
  title: string;
  body: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const btn = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    btn.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && props.onCancel();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [props]);
  return (
    <div className="modal-backdrop" onClick={props.onCancel}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="dlg-title" onClick={(e) => e.stopPropagation()}>
        <h2 id="dlg-title">{props.title}</h2>
        <div className="modal-body">{props.body}</div>
        <div className="modal-actions">
          <button className="btn btn-text" onClick={props.onCancel}>
            Cancel
          </button>
          <button ref={btn} className={`btn ${props.danger ? "btn-danger" : "btn-primary"}`} onClick={props.onConfirm}>
            {props.confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
