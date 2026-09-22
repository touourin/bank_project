// Author the finite set of replacement values; preserve source styles downstream.
import fs from 'node:fs/promises';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';

const [input, output] = process.argv.slice(2);
const values = JSON.parse(await fs.readFile(input, 'utf8'));
const workbook = Workbook.create();
const sheet = workbook.worksheets.add('Values');
const range = sheet.getRange(`A1:A${values.length}`);
range.values = values.map(value => [value]);
range.format.numberFormat = '@';
workbook.recalculate();
await (await SpreadsheetFile.exportXlsx(workbook)).save(output);
