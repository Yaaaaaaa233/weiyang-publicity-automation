// Read-only browser-log probe. Change the explicit sheet before daily use.
// This is a transport smoke test, not a complete daily-check observation.
const sheet = Application.Sheets("第二周");
const used = sheet.UsedRange;
const last = used.Row + used.Rows.Count - 1;
if (last > 300) throw new Error("scan_limit_exceeded");
const rows = [];
for (let r = 1; r <= last; r++) {
  const cells = [];
  for (let c = 1; c <= 11; c++) cells.push(String(sheet.Cells(r,c).Text || ""));
  rows.push({row:r,cells:cells});
}
const result = {schema:"weiyang.raw-sheet.v1",sheet:String(sheet.Name),generated_at:new Date().toISOString(),last_row:last,cols:11,rows:rows};
const text = JSON.stringify(result);
const total = Math.ceil(text.length / 500);
console.log("WY_JSON_BEGIN " + total + " " + text.length);
for (let i=0;i<total;i++) console.log("WY_JSON_CHUNK " + (i+1) + "/" + total + " " + text.slice(i*500,(i+1)*500));
console.log("WY_JSON_END " + total + " " + text.length);
for (const r of [6,7,13,14,62]) {
  const cell=sheet.Cells(r,1);
  const area=cell.MergeArea;
  console.log("WY_MERGE " + JSON.stringify({row:r,merged:cell.MergeCells,start_row:area.Row,end_row:area.Row+area.Rows.Count-1,start_col:area.Column,col_count:area.Columns.Count}));
}
return result;
