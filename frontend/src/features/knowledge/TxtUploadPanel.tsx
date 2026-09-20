import { useCallback, useRef, useState } from "react";
import { Alert, Button, Input, Progress } from "antd";
import { FileText } from "lucide-react";
import { errorMessage } from "../../api/request";
import { useResource } from "../../hooks/useResource";
import { ErrorNotice } from "../../ui/Feedback";
import { knowledgeApi } from "./api";
import "./knowledge.css";

export function TxtUploadPanel({
  token,
  onSaved,
}: {
  token: string;
  onSaved?: () => void;
}) {
  const config = useResource(
    useCallback(
      (signal: AbortSignal) => knowledgeApi.config(token, signal),
      [token],
    ),
  );
  const [files, setFiles] = useState<File[]>([]);
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [saved, setSaved] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const maxBytes = config.data?.max_upload_bytes ?? 20 * 1024 * 1024;
  async function save() {
    if (inFlight.current || !files.length) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    setSaved([]);
    const completed: string[] = [];
    const failures: string[] = [];
    try {
      for (const file of files) {
        try {
          if (!/\.txt$/i.test(file.name))
            throw new Error(`${file.name}：请选择 .txt 文本文件。`);
          if (!file.size || file.size > maxBytes)
            throw new Error(
              `${file.name}：文件不能为空且不能超过 ${maxBytes / 1024 / 1024} MB。`,
            );
          await knowledgeApi.upload(
            token,
            file,
            files.length === 1 ? name : file.name,
          );
          completed.push(file.name);
          setSaved([...completed]);
          setFiles((current) => current.filter((entry) => entry !== file));
        } catch (reason) {
          failures.push(`${file.name}：${errorMessage(reason)}`);
          setError(failures.join("；"));
        }
      }
      setName("");
    } finally {
      inFlight.current = false;
      setBusy(false);
      if (completed.length) onSaved?.();
    }
  }
  return (
    <div className="txt-upload-panel">
      <label className="txt-upload-zone">
        <FileText size={30} />
        <strong>选择 TXT 文本</strong>
        <span>支持多个文件 · 单文件 ≤ {maxBytes / 1024 / 1024} MB</span>
        <input
          aria-label="选择 TXT 文本文件"
          type="file"
          accept=".txt,text/plain"
          multiple
          disabled={busy}
          onChange={(event) => {
            setFiles(Array.from(event.target.files ?? []));
            setSaved([]);
            setError("");
            event.target.value = "";
          }}
        />
      </label>
      {files.length > 0 && (
        <ul className="knowledge-file-list">
          {files.map((file, index) => (
            <li key={`${file.name}-${index}`}>
              {file.name} <small>{(file.size / 1024).toFixed(1)} KB</small>
            </li>
          ))}
        </ul>
      )}
      <label className="knowledge-field">
        文档名称（可选）
        <Input
          disabled={busy || files.length > 1}
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="默认使用文件名"
        />
      </label>
      <p className="hint">
        接收原文后，前往“GraphRAG
        图谱与问答”启动索引。模型将抽取实体与关系、生成社区摘要并支持图谱问答。
      </p>
      <Button
        type="primary"
        block
        loading={busy}
        disabled={!files.length}
        onClick={() => void save()}
      >
        保存 TXT 文本{files.length ? `（${files.length} 个文件）` : ""}
      </Button>
      {busy && <Progress percent={0} status="active" showInfo={false} />}
      {error && <ErrorNotice message={error} />}
      {config.error && (
        <ErrorNotice message={config.error} onRetry={config.refresh} />
      )}
      {saved.length > 0 && (
        <Alert
          type="success"
          showIcon
          title={`已保存 ${saved.length} 个文本`}
          description={saved.join("、")}
        />
      )}
    </div>
  );
}
