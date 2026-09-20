import { useRef, useState } from "react";
import { Alert, Button, Checkbox, Form, Input, InputNumber } from "antd";
import { EmptyState, ErrorNotice } from "../../ui/Feedback";
import { errorMessage, intakeApi } from "./api";
import type { BatchDetail, CatalogTable, MysqlConnection } from "./types";

const requiredText = [
  { required: true, whitespace: true, message: "请填写此项" },
];

export function MysqlPanel({
  token,
  onSaved,
  onQueued,
  maxTables,
}: {
  token: string;
  onSaved: (batch: BatchDetail) => void;
  onQueued?: () => void;
  maxTables: number;
}) {
  const [form] = Form.useForm<MysqlConnection>();
  const [useProject, setUseProject] = useState(true);
  const [catalog, setCatalog] = useState<CatalogTable[]>();
  const [selected, setSelected] = useState<string[]>([]);
  const [operation, setOperation] = useState<"inspect" | "import" | null>(null);
  const inFlight = useRef(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const busy = operation !== null;
  function resetCatalog() {
    setCatalog(undefined);
    setSelected([]);
    setError("");
    setSuccess("");
  }
  async function inspect(connection: MysqlConnection) {
    if (inFlight.current) return;
    inFlight.current = true;
    setOperation("inspect");
    resetCatalog();
    try {
      setCatalog(
        await intakeApi.catalog(token, useProject ? null : connection),
      );
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      inFlight.current = false;
      setOperation(null);
    }
  }
  async function importTables() {
    if (inFlight.current || !selected.length || selected.length > maxTables)
      return;
    inFlight.current = true;
    setOperation("import");
    setError("");
    setSuccess("");
    try {
      const batch = await intakeApi.mysql(
        token,
        useProject ? null : form.getFieldsValue(true),
        selected,
      );
      if ("status" in batch) {
        onQueued?.();
        setSuccess("已创建后台接入任务，进度见接入任务列表");
        return;
      }
      onSaved(batch);
      setSuccess(
        `已暂存 ${batch.table_count} 张表，共 ${batch.row_count.toLocaleString()} 行。`,
      );
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      inFlight.current = false;
      setOperation(null);
    }
  }
  return (
    <Form
      form={form}
      name="mysql-connection"
      layout="vertical"
      disabled={busy}
      onFinish={inspect}
      initialValues={{
        host: "",
        port: 3306,
        database: "",
        user: "",
        password: "",
      }}
      validateTrigger="onBlur"
      onValuesChange={resetCatalog}
    >
      <Checkbox
        checked={useProject}
        onChange={(event) => {
          setUseProject(event.target.checked);
          resetCatalog();
        }}
      >
        使用项目配置的 MySQL
      </Checkbox>
      {useProject ? (
        <p className="hint">使用已配置的数据源，无需填写连接信息。</p>
      ) : (
        <div className="form-grid connection-fields">
          <Form.Item
            className="full"
            label="数据库地址"
            name="host"
            rules={requiredText}
          >
            <Input placeholder="数据库主机或 IP" autoComplete="off" />
          </Form.Item>
          <Form.Item
            label="端口"
            name="port"
            rules={[
              {
                required: true,
                type: "integer",
                min: 1,
                max: 65535,
                message: "请输入 1–65535 的整数",
              },
            ]}
          >
            <InputNumber min={1} max={65535} />
          </Form.Item>
          <Form.Item label="数据库名" name="database" rules={requiredText}>
            <Input placeholder="bank_project" autoComplete="off" />
          </Form.Item>
          <Form.Item label="用户名" name="user" rules={requiredText}>
            <Input placeholder="只读账号" autoComplete="off" />
          </Form.Item>
          <Form.Item label="密码" name="password">
            <Input.Password autoComplete="new-password" />
          </Form.Item>
        </div>
      )}
      <p className="hint">密码仅用于本次连接，不写入暂存数据。</p>
      <Button htmlType="submit" block loading={operation === "inspect"}>
        连接并列出数据表
      </Button>
      {catalog && (
        <div className="catalog">
          <div className="section-label">
            选择数据表{" "}
            <span>
              {selected.length} / {catalog.length}
            </span>
          </div>
          {!catalog.length && <EmptyState title="暂无可读取的数据表" />}
          <div className="catalog-list">
            {catalog.map((table) => (
              <Checkbox
                key={table.name}
                className="catalog-row"
                checked={selected.includes(table.name)}
                onChange={(event) =>
                  setSelected((previous) =>
                    event.target.checked
                      ? [...previous, table.name]
                      : previous.filter((name) => name !== table.name),
                  )
                }
              >
                <span className="catalog-name">
                  <strong>{table.name}</strong>
                  <small>{table.comment || "无表说明"}</small>
                </span>
                <small>约 {table.estimated_rows.toLocaleString()} 行</small>
              </Checkbox>
            ))}
          </div>
          <Button
            type="primary"
            block
            loading={operation === "import"}
            disabled={!selected.length || selected.length > maxTables}
            onClick={importTables}
          >
            读取所选表并暂存
          </Button>
          {selected.length > maxTables && (
            <ErrorNotice message={`单批次最多选择 ${maxTables} 张表。`} />
          )}
        </div>
      )}
      {error && <ErrorNotice message={error} />}
      {success && (
        <Alert
          className="ui-feedback"
          type="success"
          showIcon
          title={success}
          role="status"
        />
      )}
    </Form>
  );
}
