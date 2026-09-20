export interface Column {
  name: string;
  data_type: string;
  type_origin: "observed" | "declared";
  nullable: boolean;
  comment: string;
  primary_key: boolean;
}
export interface ForeignKey {
  name: string;
  columns: string[];
  target_schema: string;
  target_table: string;
  target_columns: string[];
}
export interface TableInfo {
  id: string;
  name: string;
  row_count: number;
  columns: Column[];
  foreign_keys: ForeignKey[];
  warnings: string[];
}
export interface BatchInfo {
  id: string;
  name: string;
  source_kind: "file" | "mysql";
  source: string;
  created_at: string;
  table_count: number;
  row_count: number;
  warnings: string[];
  sha256: string | null;
}
export interface BatchDetail extends BatchInfo {
  tables: TableInfo[];
}
export interface BatchPage {
  items: BatchInfo[];
  total: number;
  offset: number;
  limit: number;
}
export interface TablePage extends TableInfo {
  rows: { number: number; values: (string | null)[] }[];
  offset: number;
  limit: number;
}
export interface MysqlConnection {
  host: string;
  port: number;
  database: string;
  user: string;
  password: string;
}
export interface CatalogTable {
  name: string;
  comment: string;
  estimated_rows: number;
}
export interface IntakeLimits {
  max_upload_bytes: number;
  max_rows: number;
  max_tables: number;
  max_columns: number;
  max_cells: number;
}

export interface IntakeJob {
  id: string;
  created_at: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  name: string;
  size_bytes: number;
  rows_done: number;
  attempt: number;
  batch_id: string | null;
  error: string | null;
}
