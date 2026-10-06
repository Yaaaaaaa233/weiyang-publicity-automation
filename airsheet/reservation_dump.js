/**
 * 未央宣传组 · 每日推送预约表「只读」导出脚本（AirScript / 金山文档）
 *
 * 用途：把预约表当天日期块的篇目，以精确文本（逐码位）导出为 JSON，
 *       供 scripts/daily_check.py 消费，替代此前的截图视觉转录。
 *
 * 安全边界：本脚本只读。全文没有任何对 Value2 / Formula / Interior 等
 *           可写属性的赋值，不修改文档、不新增工作表、不写批注。
 *
 * 安装：金山文档打开目标表格 → 上方【效率】→【AirScript编辑工具】→ 粘贴本文件全部内容
 *
 * 调用（脚本令牌 webhook，见 docs/reservation-airsheet.md）：
 *   POST https://www.kdocs.cn/api/v3/ide/file/{file_id}/script/{script_id}/sync_task
 *   Header: AirScript-Token: {token}
 *   Header: Content-Type: application/json
 *   Body:   {"Context":{"argv":{"mode":"dump"},"sheet_name":"第二周"}}
 *
 * argv 可选项：
 *   mode      "dump"（默认，整表导出）| "day"（只导出某一天）
 *   date      mode="day" 时使用，YYYY-MM-DD
 *   sheet     工作表名；优先于平台传入的 Context.sheet_name
 *   year      A 列只写 "9月21日" 时用来补全年份，默认取运行年份
 *   cols      要读取的列号数组，默认 A:K，包含审核、发布和备注列
 *   max_rows  最多读多少行，默认 200（防止 UsedRange 因残留格式异常膨胀）
 */

// ---------------------------------------------------------------- 参数解析

const argv = (typeof Context !== 'undefined' && Context && Context.argv) ? Context.argv : {};

const mode = argv.mode || 'dump';
const wantDate = argv.date || '';
const maxRows = Number(argv.max_rows || 200);
const yearHint = String(argv.year || new Date().getFullYear());

// 列号：A=1, B=2 ... K=11。A 列始终读取，用于识别日期块。
let wantedCols = Array.isArray(argv.cols) && argv.cols.length ? argv.cols.slice() : [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11];
wantedCols = wantedCols.map(function (n) { return Number(n); }).filter(function (n) { return n >= 1 && n <= 50; });
if (wantedCols.indexOf(1) === -1) wantedCols.push(1);
wantedCols.sort(function (a, b) { return a - b; });
const colsFinal = [];
for (let i = 0; i < wantedCols.length; i++) {
  if (i === 0 || wantedCols[i] !== wantedCols[i - 1]) colsFinal.push(wantedCols[i]);
}

// ---------------------------------------------------------------- 小工具

function pad2(n) { return ('0' + n).slice(-2); }
function pad4(n) { return ('0000' + n).slice(-4); }

// 把 A 列各种写法归一成 YYYY-MM-DD；识别不了就返回空串（不猜测）。
function normDate(raw, year) {
  if (raw === null || raw === undefined) return '';
  const s = String(raw).replace(/\s+/g, '');
  if (!s) return '';
  let m = s.match(/(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})/);
  if (m) return pad4(m[1]) + '-' + pad2(m[2]) + '-' + pad2(m[3]);
  m = s.match(/^(\d{1,2})[-/.月](\d{1,2})/);
  if (m) return pad4(year) + '-' + pad2(m[1]) + '-' + pad2(m[2]);
  return '';
}

// 表格第 4 行是示例行，A 列写着「例：2024/12/5」。它的日期能被 normDate 解析出来，
// 所以必须显式排除，否则会被当成一个真实的日期块混进 date_blocks。
function isExample(v) {
  return /^\s*(例如|示例|样例|举例|例)\s*[:：]?/.test(String(v === null || v === undefined ? '' : v));
}

// Excel/WPS 日期序列号 → YYYY-MM-DD（1900 日期系统，序列号 > 60 才有效）
function serialToIso(v) {
  if (typeof v !== 'number' || !isFinite(v) || v <= 60) return '';
  const ms = Date.UTC(1899, 11, 30) + Math.round(v) * 86400000;
  const d = new Date(ms);
  return pad4(d.getUTCFullYear()) + '-' + pad2(d.getUTCMonth() + 1) + '-' + pad2(d.getUTCDate());
}

const readErrors = [];
function cellText(sheet, r, c) {
  try {
    const t = sheet.Cells(r, c).Text;
    return (t === null || t === undefined) ? '' : String(t);
  } catch (e) {
    readErrors.push({ row: r, col: c, field: 'Text' });
    return '';
  }
}

// ---------------------------------------------------------------- 定位工作表

const allSheets = [];
try {
  const ss = Application.Sheets;
  for (let i = 0; i < ss.Count; i++) allSheets.push(String(ss.Item(i + 1).Name));
} catch (e) { /* 工作表枚举失败不影响主流程 */ }

let sheet = null;
let sheetName = '';
const wantedSheet = argv.sheet || ((typeof Context !== 'undefined' && Context && Context.sheet_name) ? Context.sheet_name : '');
if (wantedSheet) {
  try { sheet = Application.Sheets(wantedSheet); } catch (e) { sheet = null; }
}
if (!sheet && !wantedSheet) {
  try { sheet = Application.ActiveSheet; } catch (e) { sheet = null; }
}
if (sheet) {
  try { sheetName = String(sheet.Name); } catch (e) { sheetName = wantedSheet || ''; }
}

if (!sheet) {
  return JSON.stringify({
    ok: false,
    script: 'reservation_dump',
    version: '1.1',
    error: 'sheet_not_found',
    message: '没能定位工作表。请用 argv.sheet 显式指定，或确认 Context.sheet_name 正确。',
    requested_sheet: wantedSheet || null,
    sheet_names: allSheets
  });
}

// ---------------------------------------------------------------- 读取区域

let lastRow = 1;
let lastCol = 1;
try {
  const used = sheet.UsedRange;
  lastRow = used.Row + used.Rows.Count - 1;
  lastCol = used.Column + used.Columns.Count - 1;
} catch (e) {
  return JSON.stringify({ ok: false, read_only: true, script: 'reservation_dump',
    version: '1.1', error: 'used_range_read_failed', sheet: sheetName });
}
if (!(lastRow >= 1)) lastRow = 1;
if (!(lastCol >= 1)) lastCol = 1;
const scanTo = Math.min(lastRow, maxRows);

const rows = [];
for (let r = 1; r <= scanTo; r++) {
  const rec = { row: r };
  let nonEmpty = false;
  for (let k = 0; k < colsFinal.length; k++) {
    const c = colsFinal[k];
    const t = cellText(sheet, r, c);
    rec['c' + c] = t;
    if (t.trim() !== '') nonEmpty = true;
  }
  if (!nonEmpty) continue;

  // A 列额外取一次原始值：若列宽过窄，.Text 可能返回 "####"，
  // 此时可用 a_value2 / a_value2_iso 兜底，避免把显示问题当成业务结论。
  try {
    const v2 = sheet.Cells(r, 1).Value2;
    rec.a_value2 = (v2 === null || v2 === undefined) ? null : v2;
    const iso = serialToIso(v2);
    if (iso) rec.a_value2_iso = iso;
  } catch (e) { /* 忽略：Text 已经够用 */ }

  rows.push(rec);
}

// ---------------------------------------------------------------- 日期块（A 列合并单元格前向填充）

const blocks = [];
const examples = [];
let currentRaw = '';
let currentNorm = '';
for (let i = 0; i < rows.length; i++) {
  const rec = rows[i];
  const a = (rec.c1 || '').trim();
  const example = isExample(a);
  rec.is_example = example;
  if (example) examples.push({ row: rec.row, text: a });
  const blockStart = (a !== '') && !example;
  if (blockStart) {
    currentRaw = a;
    currentNorm = normDate(a, yearHint) || (rec.a_value2_iso || '');
    // kind='date' 才是真正的日期块；表头、说明等 A 列有字但解析不出日期的
    // 行归为 kind='text'，避免表头里的「是否头条」「备注」被当成业务数据。
    blocks.push({
      kind: currentNorm ? 'date' : 'text',
      date_raw: currentRaw,
      date: currentNorm,
      start_row: rec.row,
      end_row: rec.row,
      row_count: 0,
      is_headline_row: null,
      headline_rows: [],
      unknown_headline_rows: [],
      remarks_present: currentNorm ? false : null
    });
  }
  rec.date_raw = currentRaw;
  rec.date = currentNorm;
  rec.block_start = blockStart;
  if (blocks.length) {
    const b = blocks[blocks.length - 1];
    b.end_row = rec.row;
    b.row_count = b.row_count + 1;
    // 只在真正的日期块里判断头条行和备注，表头块不参与
    if (b.kind === 'date') {
      const headline = (rec.c4 || '').trim();
      if (headline === '是') b.headline_rows.push(rec.row);
      else if (headline !== '' && headline !== '否') b.unknown_headline_rows.push(rec.row);
      b.is_headline_row = b.headline_rows.length === 1 && b.unknown_headline_rows.length === 0
        ? b.headline_rows[0] : null;
      if ((rec.c10 || '').trim() !== '') b.remarks_present = true;
    }
  }
}

// ---------------------------------------------------------------- 按天筛选

const dateBlocks = blocks.filter(function (b) { return b.kind === 'date'; });

let dayRows = null;
let dayBlock = null;
if (mode === 'day' && wantDate) {
  for (let i = 0; i < dateBlocks.length; i++) {
    if (dateBlocks[i].date === wantDate) { dayBlock = dateBlocks[i]; break; }
  }
  if (dayBlock) {
    dayRows = rows.filter(function (r) { return r.row >= dayBlock.start_row && r.row <= dayBlock.end_row; });
  }
}

// ---------------------------------------------------------------- 输出

const payload = {
  ok: readErrors.length === 0,
  script: 'reservation_dump',
  version: '1.1',
  read_only: true,
  read_errors: readErrors,
  scan_complete: readErrors.length === 0 && lastRow <= maxRows,
  generated_at: new Date().toISOString(),
  sheet: sheetName,
  sheet_names: allSheets,
  used_range: { last_row: lastRow, last_col: lastCol, scanned_to: scanTo, capped: lastRow > maxRows },
  cols_read: colsFinal,
  mode: mode,
  date_filter: wantDate || null,
  row_count: rows.length,
  block_count: blocks.length,
  date_block_count: dateBlocks.length,
  example_rows: examples,
  blocks: blocks,
  date_blocks: dateBlocks,
  rows: rows,
  day_found: (mode === 'day' && wantDate) ? (dayBlock !== null) : null,
  day: dayBlock ? { block: dayBlock, rows: dayRows } : null,
  notes: 'A 列为合并单元格，仅每个日期块首行有值；本脚本据此做前向填充。blocks 里 kind=date 的才是日期块，kind=text 的是表头/说明。A 列以「例：」等开头的示例行（表格第 4 行）被显式排除，不会成为日期块，见 example_rows。date_raw 为原始显示文本、未经改写，date 为归一化结果；两者不一致、date 为空或 date_raw 以 #### 结尾时需人工确认。空行不计入 rows，因此块的 end_row 是块内最后一个有内容的行。'
};

// 手动在编辑器里运行时，日志能直接看到概况。
// 注意：A 列的「说明」块可能有两千字，必须截断，否则一条日志就占满整个面板。
function brief(s, limit) {
  const t = String(s === null || s === undefined ? '' : s).replace(/\s+/g, ' ');
  return t.length > limit ? t.slice(0, limit) + '…(' + t.length + '字)' : t;
}
try {
  console.log('sheet=' + sheetName + ' rows=' + rows.length + ' blocks=' + blocks.length +
    ' date_blocks=' + dateBlocks.length + ' scanned_to=' + scanTo +
    ' examples=' + examples.length);
  for (let i = 0; i < blocks.length && i < 20; i++) {
    console.log('  [' + blocks[i].kind + '] ' + blocks[i].start_row + '-' + blocks[i].end_row +
      ' date_raw="' + brief(blocks[i].date_raw, 24) + '"' +
      ' date=' + (blocks[i].date || '(空)') +
      ' n=' + blocks[i].row_count +
      (blocks[i].is_headline_row ? ' 头条行=' + blocks[i].is_headline_row : '') +
      (blocks[i].remarks_present ? ' 有备注' : ''));
  }
} catch (e) { /* 日志失败不影响返回值 */ }

return JSON.stringify(payload);
