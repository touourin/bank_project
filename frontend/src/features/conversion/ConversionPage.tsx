import { useState } from "react";
import { Tabs } from "antd";
import { Database, FileText } from "lucide-react";
import { isDemoMode } from "../../demo";
import { WorkspaceHeading } from "../../ui/WorkspaceHeading";
import { TableConversionWorkspace } from "../alignment/TableConversionWorkspace";
import { TextConversionWorkspace } from "../knowledge/TextConversionWorkspace";
import type { GraphSourceRef } from "../knowledge/types";
import "./conversion.css";

export function ConversionPage({
  token,
  active,
  onAnalyze,
}: {
  token: string;
  active: boolean;
  onAnalyze: (source: GraphSourceRef) => void;
}) {
  const [source, setSource] = useState(isDemoMode ? "text" : "tables");
  return (
    <main className="workspace-page conversion-page">
      <WorkspaceHeading
        step="02"
        title="数据转换"
        description="选择已接入的数据，生成图谱、核对本体匹配，并按需修正转换结果。"
      />
      <ol className="conversion-steps" aria-label="数据转换操作顺序">
        {[
          ["选择数据", "表格、数据库或文本"],
          ["转换与匹配", "按来源生成对象、属性与关系"],
          ["核对结果", "查看依据，采纳或人工修正"],
        ].map(([title, description], index) => (
          <li key={title}>
            <span>{index + 1}</span>
            <div>
              <strong>{title}</strong>
              <small>{description}</small>
            </div>
          </li>
        ))}
      </ol>
      <Tabs
        className="conversion-sources"
        aria-label="转换数据类型"
        activeKey={source}
        onChange={setSource}
        items={[
          {
            key: "tables",
            label: "表格 / MySQL",
            icon: <Database size={16} />,
            disabled: isDemoMode,
            children: isDemoMode ? null : (
              <TableConversionWorkspace
                token={token}
                active={active && source === "tables"}
                onAnalyze={onAnalyze}
              />
            ),
          },
          {
            key: "text",
            label: "TXT 文本",
            icon: <FileText size={16} />,
            children: (
              <TextConversionWorkspace
                token={token}
                active={active && source === "text"}
                onAnalyze={onAnalyze}
              />
            ),
          },
        ]}
      />
    </main>
  );
}
