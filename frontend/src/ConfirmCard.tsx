// The card with the Confirm and Cancel buttons, for a big change the agent proposed (a new salary...).
//
// The agent can only PROPOSE big changes. Nothing happens until a person clicks Confirm here.
// The text on the card is the CRUD API's own summary, built from the real records, not the AI's words.

import { useState } from "react";

import { ApiError, PendingAction, cancelAction, confirmAction } from "./api";

type Props = {
  action: PendingAction;
  onSessionLost: () => void;
};

// waiting = the buttons are shown, busy = a click is being sent, then one of the three results
type Status = "waiting" | "busy" | "done" | "cancelled" | "failed";

export default function ConfirmCard({ action, onSessionLost }: Props) {
  const [status, setStatus] = useState<Status>("waiting");
  const [error, setError] = useState("");

  // "16:44", so the user knows how long they have.
  const deadline = new Date(action.expires_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

  async function handleClick(confirm: boolean) {
    setStatus("busy");
    try {
      if (confirm) {
        await confirmAction(action.id);
        setStatus("done");
      } else {
        await cancelAction(action.id);
        setStatus("cancelled");
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        onSessionLost();
        return;
      }
      // For example: it expired, or the record changed since the preview (409). The user asks again.
      setError(err instanceof Error ? err.message : "Something went wrong.");
      setStatus("failed");
    }
  }

  return (
    <div className={"confirm-card " + status}>
      <div className="confirm-title">Please check this change</div>
      <div>{action.summary}</div>

      {(status === "waiting" || status === "busy") && (
        <>
          <div className="muted small">Nothing has changed yet. Confirm before {deadline}.</div>
          <div className="confirm-buttons">
            <button onClick={() => handleClick(true)} disabled={status === "busy"}>
              Confirm
            </button>
            <button className="secondary" onClick={() => handleClick(false)} disabled={status === "busy"}>
              Cancel
            </button>
          </div>
        </>
      )}

      {status === "done" && <div className="confirm-result">Done: the change was made.</div>}
      {status === "cancelled" && <div className="confirm-result">Cancelled: nothing was changed.</div>}
      {status === "failed" && <div className="confirm-result error">Not done: {error}</div>}
    </div>
  );
}
