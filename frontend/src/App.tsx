import { useState } from "react";
import { Button, Form, Input, Tabs, Tooltip } from "antd";
import { Blocks, KeyRound, Moon, Sun } from "lucide-react";
import { IntakePage } from "./features/intake/IntakePage";
import { AlignmentPage } from "./features/alignment/AlignmentPage";
import { getHealth } from "./api/client";
import { useResource } from "./hooks/useResource";
import { useTheme } from "./hooks/useTheme";
import { ErrorNotice } from "./ui/Feedback";

export function App() {
  const health = useResource(getHealth);
  const [token, setToken] = useState("");
  const [draftToken, setDraftToken] = useState("");
  const [showToken, setShowToken] = useState(false);
  const { appearance, setTheme } = useTheme();
  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="Bank Studio 首页">
          <Blocks size={24} aria-hidden="true" />
          <span>Bank Studio</span>
        </a>
        <div className="topbar-actions">
          <span className="status" role="status">
            <i data-connected={Boolean(health.data)} />
            {health.loading
              ? "连接中"
              : health.error
                ? "服务未连接"
                : "服务已连接"}
          </span>
          <Tooltip title="设置访问凭证">
            <Button
              aria-label="设置访问凭证"
              aria-expanded={showToken}
              icon={<KeyRound size={18} />}
              onClick={() => setShowToken((value) => !value)}
            />
          </Tooltip>
          <Tooltip title={appearance === "dark" ? "切换为浅色" : "切换为深色"}>
            <Button
              aria-label="切换深浅主题"
              icon={
                appearance === "dark" ? <Sun size={19} /> : <Moon size={19} />
              }
              onClick={() => setTheme(appearance === "dark" ? "light" : "dark")}
            />
          </Tooltip>
        </div>
      </header>
      {showToken && (
        <Form
          className="token-bar"
          layout="inline"
          onFinish={() => {
            setToken(draftToken.trim());
            setShowToken(false);
          }}
        >
          <Form.Item label="访问凭证" htmlFor="access-token">
            <Input.Password
              id="access-token"
              autoComplete="off"
              value={draftToken}
              onChange={(event) => setDraftToken(event.target.value)}
              placeholder="后端启用鉴权时填写"
            />
          </Form.Item>
          <Button htmlType="submit">应用</Button>
          <span className="hint">仅在当前页面内存中使用</span>
        </Form>
      )}
      {health.error && (
        <ErrorNotice
          className="health-error"
          message={health.error}
          onRetry={health.refresh}
          retryLabel="重新连接"
        />
      )}
      <Tabs
        className="workspace-tabs"
        aria-label="工作步骤"
        defaultActiveKey="intake"
        items={[
          {
            key: "intake",
            label: "01 数据接入",
            children: <IntakePage token={token} />,
          },
          {
            key: "alignment",
            label: "02 本体对齐与图谱生成",
            children: <AlignmentPage key={token} token={token} />,
          },
        ]}
      />
      <footer>
        Bank Studio{" "}
        <span>{health.data ? `v${health.data.version}` : "数据工作空间"}</span>
      </footer>
    </div>
  );
}
