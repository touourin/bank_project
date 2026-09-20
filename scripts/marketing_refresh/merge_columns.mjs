// Author only changed cell values; the migration preserves the original package.
import fs from 'node:fs/promises';
import {Workbook, SpreadsheetFile} from '@oai/artifact-tool';

const [input, output] = process.argv.slice(2);
const patches = JSON.parse(await fs.readFile(input, 'utf8'));
const workbook = Workbook.create();
for (const [name, cells] of Object.entries(patches)) {
  const sheet = workbook.worksheets.add(name);
  for (const [address, value] of Object.entries(cells)) {
    sheet.getRange(address).values = [[value]];
  }
}
workbook.recalculate();
await (await SpreadsheetFile.exportXlsx(workbook)).save(output);
