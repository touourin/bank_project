import fs from "node:fs/promises";
import path from "node:path";
import { Workbook, SpreadsheetFile } from "@oai/artifact-tool";

const [input, output] = process.argv.slice(2);
const data = JSON.parse(await fs.readFile(input, "utf8"));
const workbook = Workbook.create();
const navy = "#243B53";
const qa = path.join(path.dirname(output), "qa");
await fs.mkdir(qa, { recursive: true });

function addTable(sheet, rows, name, top = 0) {
  const range = sheet.getRangeByIndexes(top, 0, rows.length, rows[0].length);
  range.values = rows;
  range.format.font = { name: "Arial", size: 10, color: "#243447" };
  range.format.rowHeight = 25;
  range.format.verticalAlignment = "center";
  sheet.tables.add(range, true, name).style = "TableStyleMedium2";
  sheet.getRangeByIndexes(top, 0, 1, rows[0].length).format = {
    fill: navy,
    font: { name: "Arial", size: 10, bold: true, color: "#FFFFFF" },
    rowHeight: 46,
    wrapText: true,
    verticalAlignment: "center",
  };
  sheet.showGridLines = false;
  sheet.freezePanes.freezeRows(top + 1);
  return range;
}

const sheet = workbook.worksheets.add("数据");
addTable(
  sheet,
  [data.fields.map((f) => f.key), ...data.rows],
  "SubjectSummary",
);
sheet.freezePanes.freezeColumns(2);
sheet.tabColor = "#243B53";
for (const [column, field] of data.fields.entries()) {
  const cells = sheet.getRangeByIndexes(1, column, data.rows.length, 1);
  cells.setNumberFormat(
    field.kind === "text"
      ? "@"
      : field.kind === "ratio"
        ? "0.00%"
        : ["个", "条", "家", "次", "人", "月"].includes(field.unit)
          ? "#,##0"
          : "#,##0.00",
  );
  cells.format.horizontalAlignment = field.kind === "text" ? "left" : "right";
  if (
    ["source_refs", "quality_notes", "detail_coverage", "opscope"].includes(
      field.key,
    )
  ) {
    cells.format.wrapText = true;
  }
  const width = [
    "source_refs",
    "quality_notes",
    "financial_status",
    "opscope",
  ].includes(field.key)
    ? 52
    : Math.min(40, Math.max(22, field.key.length + 2));
  sheet.getRangeByIndexes(
    0,
    column,
    data.rows.length + 1,
    1,
  ).format.columnWidth = width;
}

const notes = workbook.worksheets.add("字段说明");
notes.tabColor = "#8195A9";
notes.getRange("A1:F1").merge();
notes.getRange("A1").values = [[`${data.label}｜主体汇总与字段说明`]];
notes.getRange("A1:F1").format = {
  fill: navy,
  font: { name: "Arial", size: 16, bold: true, color: "#FFFFFF" },
  rowHeight: 38,
};
const introductions = [
  `${data.grain}。共 ${data.rows.length.toLocaleString("en-US")} 行、${data.fields.length} 个字段。`,
  `完整来源明细：${data.detail}（${data.source_tables} 张来源表、${data.source_rows.toLocaleString("en-US")} 条记录）。`,
  "数据是用于导入的静态合成测试快照；第1行是字段名。修改明细后需重新生成汇总。",
  "空白表示缺失、不适用或存在冲突，不能当成0；记录数只反映当前提供的明细覆盖。",
  "同名同义且主体、日期、口径一致才归并；个人、企业、账户、股东及交易对手的角色分别保留。",
  data.label === "征信"
    ? "财务版本冲突的金额留空，并在financial_conflicts标注；单位未明确的原表金额不与元直接相加。"
    : "主表资本已是元；股东和对外投资SUBCONAM由万元转为元。VW_GSGR个人记录不计入企业风险记录数。",
];
introductions.forEach((line, i) => {
  const range = notes.getRangeByIndexes(i + 1, 0, 1, 6);
  range.merge();
  notes.getRangeByIndexes(i + 1, 0, 1, 1).values = [[line]];
  range.format = {
    font: { name: "Arial", size: 11, color: "#526579" },
    rowHeight: 27,
    wrapText: true,
  };
});
const dictionary = [
  [
    "字段名",
    "中文含义",
    "数据类型",
    "单位 / 口径",
    "来源表与字段",
    "归并 / 计算规则",
  ],
  ...data.fields.map((f) => [
    f.key,
    f.name,
    f.kind === "text" ? "文本" : "数值",
    f.unit || "—",
    f.source,
    f.rule,
  ]),
];
addTable(notes, dictionary, "FieldDictionary", 8);
notes.getRangeByIndexes(9, 0, data.fields.length, 6).format.wrapText = true;
notes.getRangeByIndexes(9, 0, data.fields.length, 6).format.rowHeight = 68;
[39, 30, 10, 31, 70, 80].forEach((width, i) => {
  notes.getRangeByIndexes(0, i, data.fields.length + 9, 1).format.columnWidth =
    width;
});
notes.freezePanes.freezeColumns(2);

workbook.recalculate();
const inspection = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#NUM!|#SPILL!",
  options: { useRegex: true, maxResults: 20 },
  maxChars: 1000,
});
await fs.writeFile(path.join(qa, "inspection.ndjson"), inspection.ndjson);
for (const [index, view] of [
  { sheetName: "数据", range: "A1:F7" },
  { sheetName: "数据", range: "I1:N7" },
  { sheetName: "数据", range: "AA1:AG7" },
  { sheetName: "字段说明", range: "A1:F13" },
  { sheetName: "字段说明", range: "A33:F38" },
].entries()) {
  const blob = await workbook.render({ ...view, scale: 1, format: "png" });
  await fs.writeFile(
    path.join(qa, `${index}.png`),
    new Uint8Array(await blob.arrayBuffer()),
  );
}
await (await SpreadsheetFile.exportXlsx(workbook)).save(output);
console.log(
  `Exported ${data.version}/${data.label}: ${data.rows.length} rows × ${data.fields.length} fields`,
);
