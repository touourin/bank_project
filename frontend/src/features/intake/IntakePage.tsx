import { useCallback, useState } from "react";
import { Tabs } from "antd";
import { Database, FileUp } from "lucide-react";
import { useResource } from "../../hooks/useResource";
import { ErrorNotice, LoadingState } from "../../ui/Feedback";
import { Panel } from "../../ui/Panel";
import { intakeApi } from "./api";
import { BatchBrowser } from "./BatchBrowser";
import { MysqlPanel } from "./MysqlPanel";
import { IntakeJobs } from "./IntakeJobs";
import { UploadPanel } from "./UploadPanel";
import type { BatchDetail } from "./types";
import "./intake.css";

export function IntakePage({ token }: { token: string }) {
  const [savedId, setSavedId] = useState("");
  const [revision, setRevision] = useState(0);
  const loadLimits = useCallback(
    (signal: AbortSignal) => intakeApi.limits(token, signal),
    [token],
  );
  const limits = useResource(loadLimits);
  const onSaved = (batch: BatchDetail) => {
    setSavedId(batch.id);
    setRevision((value) => value + 1);
  };
  return (
    <main className="intake-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">DATA WORKSPACE / STEP 01</p>
          <h1>数据接入</h1>
          <p className="description">让分散的数据，在这里有序汇集。</p>
        </div>
        <span className="step-badge">
          <span>01</span> 第一步 · 接收与暂存
        </span>
      </div>
      <div className="intake-layout">
        <Panel
          className="source-panel"
          title="添加数据源"
          description="选择适合的数据接入方式"
          padded
        >
          {limits.error ? (
            <ErrorNotice
              message={limits.error}
              onRetry={limits.refresh}
              retryLabel="重新连接"
            />
          ) : !limits.data ? (
            <LoadingState label="读取接入配置…" />
          ) : (
            <>
              <Tabs
                className="source-tabs"
                aria-label="接入方式"
                defaultActiveKey="file"
                items={[
                  {
                    key: "file",
                    label: "文件上传",
                    icon: <FileUp size={16} />,
                    forceRender: true,
                    children: (
                      <UploadPanel
                        token={token}
                        limits={limits.data}
                        onSaved={onSaved}
                        onQueued={() => setRevision((value) => value + 1)}
                      />
                    ),
                  },
                  {
                    key: "mysql",
                    label: "MySQL 数据库",
                    icon: <Database size={16} />,
                    forceRender: true,
                    children: (
                      <MysqlPanel
                        token={token}
                        onSaved={onSaved}
                        onQueued={() => setRevision((value) => value + 1)}
                        maxTables={limits.data.max_tables}
                      />
                    ),
                  },
                ]}
              />
              <div className="limits-note">
                <strong>本次接入范围</strong>
                <p>
                  单文件 ≤ {limits.data.max_upload_bytes / 1024 / 1024} MB
                  <br />
                  单表 ≤ {limits.data.max_rows.toLocaleString()} 行 · 单批次 ≤{" "}
                  {limits.data.max_tables} 张表
                </p>
              </div>
            </>
          )}
        </Panel>
        <div className="intake-results">
          <IntakeJobs token={token} revision={revision} onSaved={onSaved} />
          <BatchBrowser token={token} savedId={savedId} revision={revision} />
        </div>
      </div>
    </main>
  );
}
