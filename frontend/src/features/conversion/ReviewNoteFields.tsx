import { Input } from "antd";

export function ReviewNoteFields({
  reviewer,
  note,
  onReviewer,
  onNote,
  noteLabel = "修改原因",
  maxLength = 2000,
}: {
  reviewer: string;
  note: string;
  onReviewer: (value: string) => void;
  onNote: (value: string) => void;
  noteLabel?: string;
  maxLength?: number;
}) {
  return (
    <>
      <label className="review-field">
        审核人
        <Input
          aria-label="审核人"
          value={reviewer}
          maxLength={100}
          onChange={(event) => onReviewer(event.target.value)}
        />
      </label>
      <label className="review-field">
        {noteLabel}
        <Input.TextArea
          aria-label={noteLabel}
          value={note}
          maxLength={maxLength}
          rows={2}
          onChange={(event) => onNote(event.target.value)}
        />
      </label>
    </>
  );
}
