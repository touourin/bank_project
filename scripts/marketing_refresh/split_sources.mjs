import fs from "node:fs/promises";
import path from "node:path";
import { Workbook, SpreadsheetFile } from "@oai/artifact-tool";

const [input, output] = process.argv.slice(2);
const data = JSON.parse(await fs.readFile(input, "utf8"));
const wb = Workbook.create();
const navy = "#243B53";
const qa = path.join(path.dirname(output), "qa");
await fs.mkdir(qa, { recursive: true });

function tableStyle(sheet, range, name, headerRow) {
  range.format.font = { name: "Arial", size: 10, color: "#243447" };
  range.format.rowHeight = 22;
  range.format.verticalAlignment = "center";
  const table = sheet.tables.add(range, true, name);
  table.style = "TableStyleMedium2";
  const header = sheet.getRangeByIndexes(
    headerRow,
    0,
    1,
    range.values[0].length,
  );
  header.format = {
    fill: navy,
    font: { name: "Arial", size: 10, bold: true, color: "#FFFFFF" },
    rowHeight: 36,
    wrapText: true,
    horizontalAlignment: "center",
    verticalAlignment: "center",
  };
  sheet.showGridLines = false;
  return table;
}

function introduction(sheet, title, lines) {
  sheet.getRange("A2").values = [[title]];
  sheet.getRange("A2").format.font = {
    name: "Arial",
    size: 14,
    bold: true,
    color: navy,
  };
  sheet.getRange("A2").format.rowHeight = 28;
  for (let i = 0; i < lines.length; i++) {
    const r = sheet.getRangeByIndexes(i + 2, 0, 1, 1);
    r.values = [[lines[i]]];
    r.format.font = { name: "Arial", size: 10, color: "#526579" };
    r.format.rowHeight = 24;
  }
}

const directory = wb.worksheets.add("目录说明");
introduction(directory, `${data.label}来源表目录`, [
  `${data.tables.length} 张来源表，${data.row_count.toLocaleString("en-US")} 条合成记录。每张表独立保存，业务数据未重新生成。`,
  "每个数据 Sheet 的第 1 行是字段名。source_row 保留原表行号，Sheet 名即来源表名。",
  "字段定义按来源表保存在“数据说明”页；证件、账号和编码保持文本。",
  `来源：《${data.source_dictionary}》。`,
]);
const entries = [
  ["序号", "来源表 / Sheet", "业务说明", "记录数", "业务字段数"],
];
for (const [i, t] of data.tables.entries())
  entries.push([
    i + 1,
    t.name,
    t.description,
    t.rows.length,
    t.columns.length - 1,
  ]);
const directoryRange = directory.getRangeByIndexes(6, 0, entries.length, 5);
directoryRange.values = entries;
tableStyle(directory, directoryRange, "SourceDirectory", 6);
[9, 43, 64, 14, 14].forEach(
  (w, i) =>
    (directory.getRangeByIndexes(
      0,
      i,
      entries.length + 6,
      1,
    ).format.columnWidth = w),
);
directory.freezePanes.freezeRows(7);
directory
  .getRangeByIndexes(7, 3, data.tables.length, 2)
  .setNumberFormat("#,##0");
directory.tabColor = "#54718A";

const visuals = [];
for (const [index, t] of data.tables.entries()) {
  const sheet = wb.worksheets.add(t.name);
  const range = sheet.getRangeByIndexes(
    0,
    0,
    t.rows.length + 1,
    t.columns.length,
  );
  range.values = [t.columns, ...t.rows];
  tableStyle(sheet, range, `Source_${index + 1}`, 0);
  for (const [j, c] of t.columns.entries()) {
    const values = sheet.getRangeByIndexes(1, j, t.rows.length, 1);
    values.setNumberFormat(t.formats[c]);
    const displayWidth = (s) =>
      [...String(s ?? "")].reduce(
        (n, c) => n + (c.charCodeAt(0) > 255 ? 2 : 1),
        0,
      );
    const sampleWidth = Math.max(
      ...t.rows.slice(0, 40).map((r) => displayWidth(r[j])),
    );
    const width = Math.min(
      44,
      Math.max(
        c === "source_row" ? 12 : 18,
        displayWidth(c) + 2,
        Math.min(sampleWidth + 2, 40),
      ),
    );
    sheet.getRangeByIndexes(0, j, t.rows.length + 1, 1).format.columnWidth =
      width;
  }
  sheet.freezePanes.freezeRows(1);
  sheet.freezePanes.freezeColumns(Math.min(2, t.columns.length));
  visuals.push({
    sheet: t.name,
    range: `A1:${String.fromCharCode(64 + Math.min(5, t.columns.length))}${Math.min(5, t.rows.length + 1)}`,
  });
  console.log(
    `Authored ${data.version}/${data.label} ${index + 1}/${data.tables.length}: ${t.name}`,
  );
}

const notes = wb.worksheets.add("数据说明");
introduction(notes, `${data.label}字段与来源说明`, [
  `${data.tables.length} 张来源表分别保存。相同字段在不同来源表中分别列出定义，不表示一个 Sheet 有重复列。`,
  `来源：《${data.source_dictionary}》。保留已统一的字段名称、类型与金额单位。`,
  "来源表名由 Sheet 名承载；source_row 与 Sheet 名共同定位拆分前的原始记录。",
  "空白为缺失或不适用，0 为已知零值。当前数据仍为合成测试数据。",
  "原字段、原类型和字典位置保留在 E、F、I 列。筛选 A 列可查看对应 Sheet 的完整字段定义。",
]);
const noteRange = notes.getRangeByIndexes(8, 0, data.dictionary.length, 10);
noteRange.values = data.dictionary;
tableStyle(notes, noteRange, "SourceFieldDictionary", 8);
[42, 30, 46, 22, 27, 23, 27, 14, 48, 72].forEach(
  (w, i) =>
    (notes.getRangeByIndexes(
      0,
      i,
      data.dictionary.length + 8,
      1,
    ).format.columnWidth = w),
);
notes.freezePanes.freezeRows(9);
notes.freezePanes.freezeColumns(2);

wb.recalculate();
const errors = await wb.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#NUM!|#SPILL!",
  options: { useRegex: true, maxResults: 20 },
  maxChars: 1000,
});
await fs.writeFile(path.join(qa, "inspection.ndjson"), errors.ndjson);
for (const [i, view] of [
  { sheet: "目录说明", range: "A2:E12" },
  ...visuals,
  { sheet: "数据说明", range: "A9:F15" },
].entries()) {
  const blob = await wb.render({
    sheetName: view.sheet,
    range: view.range,
    scale: 1,
    format: "png",
  });
  await fs.writeFile(
    path.join(qa, `${String(i).padStart(3, "0")}.png`),
    new Uint8Array(await blob.arrayBuffer()),
  );
  if (i % 20 === 0)
    console.log(
      `Rendered ${data.version}/${data.label}: ${i + 1}/${visuals.length + 2}`,
    );
}
await fs.writeFile(
  path.join(qa, "sheets.json"),
  JSON.stringify(["目录说明", ...visuals.map((x) => x.sheet), "数据说明"]),
);
await (await SpreadsheetFile.exportXlsx(wb)).save(output);
console.log(`Exported ${data.version}/${data.label}: ${output}`);
