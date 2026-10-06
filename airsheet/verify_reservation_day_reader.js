const fs=require('fs');
const assert=require('assert/strict');
const path=require('path');
const source=fs.readFileSync(path.join(__dirname,'reservation_day_reader.js'),'utf8');
function sheet(name='虚构周') {
  return {Name:name,UsedRange:{Row:1,Rows:{Count:65}},Cells(r,c){
    return {Text:c===1&&r===6?'2026/10/4\n周日':c===1&&r===5?'例：2024/12/5':'',
      MergeArea:{Row:r===6?6:r,Column:1,Rows:{Count:r===6?8:1},Columns:{Count:1}}};
  }};
}
function run(sheets,date='2026-10-04') {
  const logs=[];
  const collection={Count:sheets.length,Item(i){return sheets[i-1];}};
  const result=new Function('Application','console','Context',source)({Sheets:collection},{log(s){logs.push(s);}},{argv:{date}});
  assert(logs[0].startsWith('WY_BEGIN '+result.run_id+' '));
  assert(logs.at(-1).startsWith('WY_END '+result.run_id+' '));
  const payload=logs.slice(1,-1).map(s=>s.match(/^WY_PART \S+ \d+\/\d+ ([\s\S]*)$/)[1]).join('');
  assert.deepEqual(JSON.parse(payload),result);
  return result;
}
const ok=run([sheet()]);
assert.equal(ok.ok,true);assert.equal(ok.matches[0].rows.length,8);assert.equal(ok.sheets_scanned[0].dates.length,1);
assert.equal(run([sheet()],'2026-09-01').error,'date_not_found');
assert.equal(run([sheet(),sheet('另一个周')]).error,'multiple_date_blocks');
let broken=sheet();broken.UsedRange.Rows.Count=301;assert.equal(run([broken]).error,'row_scan_limit');
broken=sheet();broken.Cells=()=>{throw new Error('read_failed');};assert.equal(run([broken]).error,'read_failed');
broken=sheet();const old=broken.Cells;broken.Cells=(r,c)=>{const cell=old(r,c);if(r===6)cell.MergeArea.Rows.Count=100;return cell;};assert.equal(run([broken]).error,'unexpected_date_merge');
broken=sheet();broken.Cells=(r,c)=>{const cell=old(r,c);if(r===14&&c===1)cell.Text='####';return cell;};assert.equal(run([broken]).error,'unrecognized_date_cell');
assert.equal(run([sheet()],'2026-02-30').error,'invalid_date');
// 工作表配额：周表数量才是真实上限（每周新增一张表），平台内部表不占配额。
// 只设上限不设下限——命名不符时仍应走正常扫描并报 date_not_found，而不是误报配额错误。
function blank(name) {
  return {Name:name,UsedRange:{Row:1,Rows:{Count:65}},Cells(){return {Text:'',
    MergeArea:{Row:1,Column:1,Rows:{Count:1},Columns:{Count:1}}};}};
}
function weeks(n,withDateAt) {
  const out=[];for(let i=1;i<=n;i++)out.push(i===withDateAt?sheet('第'+i+'周'):blank('第'+i+'周'));
  return out;
}
assert.equal(run(weeks(60,60)).ok,true);
assert.equal(run(weeks(60,60).concat([blank('WpsReserved_CellImgList'),blank('WpsReserved_CellImgList2')])).ok,true);
assert.equal(run(weeks(61,61)).error,'week_sheet_limit');
assert.equal(run(weeks(60,60).concat(Array.from({length:70},(_,i)=>blank('extra'+i)))).error,'sheet_scan_limit');
console.log('12 AirScript day-reader scenarios passed');
