import fs from 'node:fs/promises';
import {Workbook, SpreadsheetFile, FileBlob} from '@oai/artifact-tool';

const [mode, input, output, metadataPath, label] = process.argv.slice(2);
if (mode === 'patch') {
  const patches=JSON.parse(await fs.readFile(input,'utf8'));
  const wb=Workbook.create();
  for(const [name,cells] of Object.entries(patches)){
    const sheet=wb.worksheets.add(name);
    for(const [address,value] of Object.entries(cells))sheet.getRange(address).values=[[value]];
  }
  const file=await SpreadsheetFile.exportXlsx(wb);await file.save(output);
} else if (mode === 'preview') {
  const wb = await SpreadsheetFile.importXlsx(await FileBlob.load(input));
  try{
    const textCells=JSON.parse(await fs.readFile(input.replace(/\.xlsx$/,'.text.json'),'utf8'));
    const sheet=wb.worksheets.getItem('数据');
    for(const [address,value] of Object.entries(textCells)){
      sheet.getRange(address).values=[[value]];
      sheet.getRange(address).format.numberFormat='@';
    }
  }catch(error){if(error.code!=='ENOENT')throw error;}
  const image = await wb.render({sheetName:metadataPath || '数据', range:label || 'A1:H9', scale:1.4, format:'png'});
  await fs.writeFile(output, new Uint8Array(await image.arrayBuffer()));
  console.log('Rendered', label);
} else {
  const meta = JSON.parse(await fs.readFile(metadataPath,'utf8'))[label];
  const records = (await fs.readFile(input,'utf8')).trimEnd().split('\n').map(JSON.parse);
  const letter = n => {let s=''; while(n){const r=(n-1)%26;s=String.fromCharCode(65+r)+s;n=Math.floor((n-1)/26)} return s;};
  const convert = (r,k,i) => {
    if(r[k] === undefined || r[k] === '') return null;
    const kind=meta.types[letter(i+1)] || 'text';
    if(kind==='date' || kind==='datetime') {
      const ms=Date.parse(r[k].replace(' ','T')+(r[k].includes(' ')?'Z':''));
      if(!Number.isFinite(ms)) throw new Error(`Invalid date ${k}: ${r[k]}`);
      return ms/86400000+25569;
    }
    if(kind==='number') {
      const value=Number(r[k]);if(!Number.isFinite(value)) throw new Error(`Invalid numeric ${k}: ${r[k]}`);return value;
    }
    return r[k];
  };
  const wb=Workbook.create();const sheet=wb.worksheets.add('数据');
  // Sparse source tables share a very wide union schema. Author only occupied
  // column runs for each contiguous source-table group, preserving nulls without
  // allocating millions of empty cells.
  let start=0;
  while(start<records.length){
    let end=start+1;
    while(end<records.length && records[end].source_table===records[start].source_table) end++;
    const used=new Set();
    for(let row=start;row<end;row++) for(const k of Object.keys(records[row])) if(records[row][k]!=='' && records[row][k]!==null) used.add(k);
    const occupied=meta.columns.map((k,i)=>used.has(k)?i:-1).filter(i=>i>=0);
    let x=0;
    while(x<occupied.length){
      let y=x+1;while(y<occupied.length && occupied[y]===occupied[y-1]+1)y++;
      const first=occupied[x],width=y-x;
      const values=[];
      for(let row=start;row<end;row++)values.push(meta.columns.slice(first,first+width).map((k,j)=>convert(records[row],k,first+j)));
      sheet.getRangeByIndexes(start,first,end-start,width).values=values;
      x=y;
    }
    start=end;
  }
  wb.recalculate();
  const file=await SpreadsheetFile.exportXlsx(wb);await file.save(output);
  console.log(JSON.stringify({rows:records.length,columns:meta.columns.length,output}));
}
