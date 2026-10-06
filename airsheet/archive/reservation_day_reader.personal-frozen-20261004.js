// Read-only WPS browser entry. Empty date means today's Asia/Shanghai date.
// For an explicitly authorized historical test, replace only this constant.
const REQUESTED_DATE = "";
const started = new Date();
const requested = REQUESTED_DATE || new Date(started.getTime()+8*3600000).toISOString().slice(0,10);
const runId = "wps-" + started.getTime();
function text(sheet,r,c) { const value=sheet.Cells(r,c).Text; return value==null ? "" : String(value); }
function cells(sheet,r) { const values=[]; for(let c=1;c<=11;c++) values.push(text(sheet,r,c)); return {row:r,cells:values}; }
function dateOf(value) {
  const m=String(value).trim().match(/^(\d{4})[\/.\-年](\d{1,2})[\/.\-月](\d{1,2})(?:日|\s|$)/);
  if(!m) return "";
  const iso=m[1]+"-"+("0"+m[2]).slice(-2)+"-"+("0"+m[3]).slice(-2);
  if(new Date(iso+"T00:00:00Z").toISOString().slice(0,10)!==iso) throw new Error("invalid_date");
  return iso;
}
let result;
try {
  if(!/^\d{4}-\d{2}-\d{2}$/.test(requested) || dateOf(requested)!==requested) throw new Error("invalid_query_date");
  const count=Application.Sheets.Count;
  if(!(count>0 && count<=12)) throw new Error("sheet_scan_limit");
  const scanned=[];
  const matches=[];
  for(let i=1;i<=count;i++) {
    const sheet=Application.Sheets.Item(i);
    const name=String(sheet.Name);
    const used=sheet.UsedRange;
    const last=used.Row+used.Rows.Count-1;
    if(!(last>=1 && last<=300)) throw new Error("row_scan_limit");
    const dates=[];
    for(let r=1;r<=last;r++) {
      const a=text(sheet,r,1);
      if(/^\s*(例如|示例|样例|举例|例)\s*[:：]?/.test(a)) continue;
      const date=dateOf(a);
      if(!date) {
        if(r>5 && /^\s*(?:#|\d{1,4}[年月\/.-]\d)/.test(a)) throw new Error("unrecognized_date_cell");
        continue;
      }
      const cell=sheet.Cells(r,1);
      const area=cell.MergeArea;
      const start=area.Row;
      const end=start+area.Rows.Count-1;
      if(start!==r || area.Column!==1 || area.Columns.Count!==1 || end>last) throw new Error("unexpected_date_merge");
      dates.push({date:date,start_row:start,end_row:end});
      if(date===requested) {
        const rows=[];
        for(let rr=start;rr<=end;rr++) {
          const entry=cells(sheet,rr);
          if(rr>start && entry.cells[0].trim()) throw new Error("date_merge_interior_not_empty");
          rows.push(entry);
        }
        matches.push({sheet:name,start_row:start,end_row:end,headers:[cells(sheet,3),cells(sheet,4)],instructions:text(sheet,2,1),rows:rows});
      }
      r=end;
    }
    scanned.push({sheet:name,last_row:last,dates:dates});
  }
  result={schema:"weiyang.reservation-day.v1",ok:matches.length===1,run_id:runId,generated_at:started.toISOString(),date:requested,cols:11,sheets_scanned:scanned,matches:matches,error:matches.length===1 ? null : matches.length===0 ? "date_not_found" : "multiple_date_blocks"};
} catch(error) {
  result={schema:"weiyang.reservation-day.v1",ok:false,run_id:runId,generated_at:started.toISOString(),date:requested,error:String(error.message||error)};
}
const payload=JSON.stringify(result);
let hash=2166136261;
for(let n=0;n<payload.length;n++) hash=Math.imul(hash^payload.charCodeAt(n),16777619)>>>0;
const checksum=("00000000"+hash.toString(16)).slice(-8);
const chunks=Math.ceil(payload.length/400);
console.log("WY_BEGIN "+runId+" "+chunks+" "+payload.length+" "+checksum);
for(let n=0;n<chunks;n++) console.log("WY_PART "+runId+" "+(n+1)+"/"+chunks+" "+payload.slice(n*400,(n+1)*400));
console.log("WY_END "+runId+" "+chunks+" "+payload.length+" "+checksum);
return result;
