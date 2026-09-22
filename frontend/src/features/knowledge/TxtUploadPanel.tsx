import { useCallback, useRef, useState } from "react";
import { Alert, Button, Input, InputNumber, Select, Progress } from "antd";
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
  const [profile, setProfile] = useState("enterprise_zh");
  const [chunkSize, setChunkSize] = useState(1000);
  const [overlap, setOverlap] = useState(150);
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
            {
              profile,
              chunk_size: String(chunkSize),
              overlap: String(overlap),
            },
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
      <label className="knowledge-field">
        建图方案
        <Select
          aria-label="建图方案"
          value={profile}
          disabled={busy}
          options={(
            config.data?.profiles ?? [
              { id: "enterprise_zh", name: "企业情报 · 原项目中文方案" },
              { id: "general", name: "通用文档 · 跟随原文语言" },
            ]
          ).map((p) => ({ value: p.id, label: p.name }))}
          onChange={(value) => {
            setProfile(value);
            const p = config.data?.profiles?.find((p) => p.id === value);
            setChunkSize(p?.chunk_size ?? (value === "general" ? 1200 : 1000));
            setOverlap(p?.overlap ?? (value === "general" ? 100 : 150));
          }}
        />
      </label>
      <label className="knowledge-field">
        分块大小（token）
        <InputNumber
          aria-label="分块大小"
          min={100}
          max={16000}
          value={chunkSize}
          onChange={(v) => setChunkSize(v ?? 1000)}
        />
      </label>
      <label className="knowledge-field">
        重叠 token
        <InputNumber
          aria-label="重叠 token"
          min={0}
          max={chunkSize - 1}
          value={overlap}
          onChange={(v) => setOverlap(v ?? 150)}
        />
      </label>
      <p className="hint">
        企业方案沿用原项目八类实体及中文业务提示词。已完成的历史索引保留原方案；切换方案需重新接入原文建图。
      </p>
      <p className="hint">
        接收原文后，前往第二步“数据转换”，选择 TXT
        文本开始转换。转换完成后可核对实体关系与本体匹配。
      </p>
      <Button
        type="primary"
        block
        loading={busy}
        disabled={!files.length || overlap >= chunkSize}
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
