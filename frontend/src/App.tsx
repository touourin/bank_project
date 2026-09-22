import { useState } from "react";
import { Button, Form, Input, Tabs, Tag, Tooltip } from "antd";
import {
  ArrowRight,
  Blocks,
  KeyRound,
  Moon,
  RotateCcw,
  Sun,
} from "lucide-react";
import { IntakePage } from "./features/intake/IntakePage";
import { ConversionPage } from "./features/conversion/ConversionPage";
import { AnalysisPage } from "./features/analysis/AnalysisPage";
import type { GraphSourceRef } from "./features/knowledge/types";
import { ResolutionPage } from "./features/knowledge/ResolutionPage";
import { RiskPage } from "./features/risk/RiskPage";
import { getHealth } from "./api/client";
import { useResource } from "./hooks/useResource";
import { useTheme } from "./hooks/useTheme";
import { ErrorNotice } from "./ui/Feedback";
import { demoStartStep, isDemoMode } from "./demo";

const loadHealth = isDemoMode
  ? () => Promise.resolve({ status: "ok" as const, version: "前端演示" })
  : getHealth;

export function App() {
  const health = useResource(loadHealth);
  const [activeStep, setActiveStep] = useState(
    isDemoMode ? demoStartStep : "intake",
  );
  const [selectedGraph, setSelectedGraph] = useState<GraphSourceRef>();
  const [token, setToken] = useState("");
  const [draftToken, setDraftToken] = useState("");
  const [showToken, setShowToken] = useState(false);
  const { appearance, setTheme } = useTheme();
  function changeStep(key: string) {
    setActiveStep(key);
    if (isDemoMode) {
      const url = new URL(window.location.href);
      url.searchParams.delete("step");
      url.searchParams.set("workspace", key);
      window.history.replaceState(null, "", url);
    }
  }
  function openAnalysis(source: GraphSourceRef) {
    setSelectedGraph(source);
    changeStep("analysis");
  }
  const nextStep =
    activeStep === "conversion"
      ? { key: "resolution", label: "下一步：实体消歧" }
      : activeStep === "resolution"
        ? { key: "analysis", label: "下一步：图谱分析" }
        : { key: "conversion", label: "返回数据转换" };
  return (
    <div className="app-shell">
      <header className="topbar">
        <a
          className="brand"
          href={isDemoMode ? "?demo=1" : "/"}
          aria-label="Bank Studio 首页"
        >
          <Blocks size={24} aria-hidden="true" />
          <span>Bank Studio</span>
        </a>
        <div className="topbar-actions">
          <span className="status" role="status">
            <i data-connected={Boolean(health.data)} />
            {isDemoMode
              ? "前端演示"
              : health.loading
                ? "连接中"
                : health.error
                  ? "服务未连接"
                  : "服务已连接"}
          </span>
          {!isDemoMode && <Button href="?demo=1">查看演示版</Button>}
          {!isDemoMode && (
            <Tooltip title="设置访问凭证">
              <Button
                aria-label="设置访问凭证"
                aria-expanded={showToken}
                icon={<KeyRound size={18} />}
                onClick={() => setShowToken((value) => !value)}
              />
            </Tooltip>
          )}
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
            setSelectedGraph(undefined);
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
      {isDemoMode && (
        <section className="demo-banner" aria-label="前端演示说明">
          <div>
            <div className="demo-banner-title">
              <Tag color="green">演示模式</Tag>
              <strong>数据转换 → 实体消歧 → 图谱分析</strong>
            </div>
            <p>
              已载入示例数据，可浏览图谱、体验问答和合并核验。操作仅在当前页面生效，刷新即可还原。
            </p>
          </div>
          <div className="demo-banner-actions">
            <Button
              icon={<RotateCcw size={15} />}
              onClick={() => window.location.reload()}
            >
              重置演示
            </Button>
            <Button
              type="primary"
              icon={<ArrowRight size={15} />}
              onClick={() => changeStep(nextStep.key)}
            >
              {nextStep.label}
            </Button>
            <Button type="text" href="?">
              退出演示
            </Button>
          </div>
        </section>
      )}
      <Tabs
        className="workspace-tabs"
        aria-label="工作步骤"
        activeKey={activeStep}
        onChange={changeStep}
        items={[
          {
            key: "intake",
            label: "01 数据接入",
            disabled: isDemoMode,
            children: isDemoMode ? null : <IntakePage token={token} />,
          },
          {
            key: "conversion",
            label: "02 数据转换",
            children: (
              <ConversionPage
                key={token}
                token={token}
                active={activeStep === "conversion"}
                onAnalyze={openAnalysis}
              />
            ),
          },
          {
            key: "resolution",
            label: "03 实体消歧",
            children: (
              <ResolutionPage
                key={token}
                token={token}
                active={activeStep === "resolution"}
                onAnalyze={openAnalysis}
              />
            ),
          },
          {
            key: "analysis",
            label: "04 图谱浏览与分析",
            children: (
              <AnalysisPage
                key={token}
                token={token}
                active={activeStep === "analysis"}
                selected={selectedGraph}
                onSelect={setSelectedGraph}
              />
            ),
          },
          {
            key: "risk",
            label: "05 风险规则",
            disabled: isDemoMode,
            children: isDemoMode ? null : (
              <RiskPage
                key={token}
                token={token}
                active={activeStep === "risk"}
              />
            ),
          },
        ]}
      />
      <footer>
        Bank Studio{" "}
        <span>
          {isDemoMode
            ? "示例数据 · 仅供界面演示"
            : health.data
              ? `v${health.data.version}`
              : "数据工作空间"}
        </span>
      </footer>
    </div>
  );
}
