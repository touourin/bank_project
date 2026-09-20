import { useRef, useState } from "react";
import {
  Alert,
  Button,
  Checkbox,
  Collapse,
  Form,
  InputNumber,
  Select,
  Upload,
} from "antd";
import type { RcFile } from "antd/es/upload/interface";
import { FileUp } from "lucide-react";
import { ErrorNotice } from "../../ui/Feedback";
import { errorMessage, intakeApi } from "./api";
import type { BatchDetail, IntakeLimits } from "./types";

interface Props {
  token: string;
  limits: IntakeLimits;
  onSaved: (batch: BatchDetail) => void;
  onQueued?: () => void;
}
export function UploadPanel({ token, limits, onSaved, onQueued }: Props) {
  const [files, setFiles] = useState<RcFile[]>([]);
  const [headerRow, setHeaderRow] = useState<number | null>(1);
  const [encoding, setEncoding] = useState("auto");
  const [delimiter, setDelimiter] = useState("auto");
  const [skipDescriptions, setSkipDescriptions] = useState(true);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const [results, setResults] = useState<
    { name: string; message: string; ok: boolean }[]
  >([]);
  const [error, setError] = useState("");
  const validHeader =
    headerRow !== null &&
    Number.isInteger(headerRow) &&
    headerRow >= 1 &&
    headerRow <= 100;
  function choose(chosen: RcFile[]) {
    if (inFlight.current) return;
    setError("");
    setResults([]);
    setFiles([]);
    if (chosen.length > 30) {
      setError("一次最多选择 30 个文件");
    } else if (chosen.some((file) => !/\.(xlsx|csv)$/i.test(file.name))) {
      setError("请选择 .xlsx 或 .csv 文件");
    } else if (chosen.some((file) => file.size > limits.max_upload_bytes)) {
      setError(`单文件不能超过 ${limits.max_upload_bytes / 1024 / 1024} MB`);
    } else {
      setFiles(chosen);
    }
  }
  async function upload() {
    if (inFlight.current || !files.length || !validHeader) return;
    inFlight.current = true;
    setBusy(true);
    setResults([]);
    setError("");
    try {
      for (const file of files) {
        try {
          const batch = await intakeApi.upload(token, file, {
            header_row: headerRow,
            encoding,
            delimiter,
            skip_description_sheets: skipDescriptions,
          });
          if ("status" in batch) {
            onQueued?.();
            setResults((previous) => [
              ...previous,
              {
                name: file.name,
                message: "已上传，后台处理进度见接入任务",
                ok: true,
              },
            ]);
            continue;
          }
          onSaved(batch);
          setResults((previous) => [
            ...previous,
            {
              name: file.name,
              message: `${batch.table_count} 张表 · ${batch.row_count.toLocaleString()} 行，已暂存`,
              ok: true,
            },
          ]);
        } catch (reason) {
          setResults((previous) => [
            ...previous,
            { name: file.name, message: errorMessage(reason), ok: false },
          ]);
        }
      }
    } finally {
      inFlight.current = false;
      setBusy(false);
      setFiles([]);
    }
  }
  return (
    <Form
      name="file-upload"
      layout="vertical"
      disabled={busy}
      onFinish={upload}
    >
      <Upload.Dragger
        className="upload-zone"
        aria-label="选择数据文件"
        hasControlInside
        accept=".xlsx,.csv"
        multiple
        disabled={busy}
        fileList={files.map((file) => ({
          uid: file.uid,
          name: file.name,
          size: file.size,
          originFileObj: file,
        }))}
        beforeUpload={(file, chosen) => {
          // Validate the whole selection once. Upload starts only on explicit submission.
          if (file === chosen[0]) choose(chosen);
          return Upload.LIST_IGNORE;
        }}
        onRemove={(file) => {
          if (!inFlight.current)
            setFiles((previous) =>
              previous.filter((item) => item.uid !== file.uid),
            );
        }}
      >
        <Button
          type="text"
          block
          className="upload-trigger"
          aria-label="选择数据文件"
        >
          <FileUp size={32} strokeWidth={1.4} aria-hidden="true" />
          <span className="upload-title">拖入表格，或点击选择</span>
          <span className="hint">Excel / CSV · 可选择多个文件</span>
        </Button>
      </Upload.Dragger>
      <p className="hint">
        每个文件保存为独立批次；Excel 中每个 Sheet 保存为一张表。
      </p>
      <div className="parse-options">
        <Form.Item
          label="表头行号"
          required
          htmlFor="upload-header-row"
          help={!validHeader ? "请输入 1–100 的整数" : undefined}
          validateStatus={!validHeader ? "error" : undefined}
        >
          <InputNumber
            id="upload-header-row"
            min={1}
            max={100}
            value={headerRow}
            onChange={setHeaderRow}
          />
        </Form.Item>
        <Checkbox
          checked={skipDescriptions}
          onChange={(event) => setSkipDescriptions(event.target.checked)}
        >
          跳过说明页
        </Checkbox>
        <p className="hint">跳过名称以“说明”结尾，或名为 README 的 Sheet。</p>
        <Collapse
          ghost
          size="small"
          items={[
            {
              key: "csv",
              label: "CSV 读取选项",
              children: (
                <div className="form-grid">
                  <Form.Item label="文件编码" htmlFor="upload-encoding">
                    <Select
                      id="upload-encoding"
                      value={encoding}
                      onChange={setEncoding}
                      options={[
                        { value: "auto", label: "自动识别" },
                        { value: "utf-8-sig", label: "UTF-8" },
                        { value: "gb18030", label: "GB18030 / GBK" },
                      ]}
                    />
                  </Form.Item>
                  <Form.Item label="分隔符" htmlFor="upload-delimiter">
                    <Select
                      id="upload-delimiter"
                      value={delimiter}
                      onChange={setDelimiter}
                      options={[
                        { value: "auto", label: "自动识别" },
                        { value: "comma", label: "逗号" },
                        { value: "tab", label: "制表符" },
                        { value: "semicolon", label: "分号" },
                      ]}
                    />
                  </Form.Item>
                </div>
              ),
            },
          ]}
        />
      </div>
      {error && <ErrorNotice message={error} />}
      <Button
        type="primary"
        htmlType="submit"
        block
        loading={busy}
        disabled={!files.length || !validHeader}
      >
        {busy
          ? "正在上传…"
          : `解析并暂存${files.length ? `（${files.length} 个文件）` : ""}`}
      </Button>
      <div className="upload-results" aria-live="polite">
        {results.map((result, index) => (
          <Alert
            key={index}
            className={result.ok ? "result-card" : "result-card failed"}
            showIcon
            type={result.ok ? "success" : "error"}
            title={result.name}
            description={result.message}
          />
        ))}
      </div>
    </Form>
  );
}
