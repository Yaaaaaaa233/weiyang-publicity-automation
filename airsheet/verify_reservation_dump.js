/**
 * 离线验证 reservation_dump.js —— 用模拟的金山文档对象模型跑真实分支。
 * 运行：node airsheet/verify_reservation_dump.js
 * 不连接任何真实文档，纯本地。
 */
const fs = require('fs');
const path = require('path');

const SRC = fs.readFileSync(path.join(__dirname, 'reservation_dump.js'), 'utf8');
const run = new Function('Application', 'Context', 'console', SRC);

// ---------------------------------------------------------------- 模拟对象模型
// 结构照抄真实预约表：3-4 行跨行表头；A 列日期合并（仅块首有值）；
// 两个日期块；其中一个日期单元格故意让 .Text 返回 '####'（列宽过窄）。

function serial(iso) {
  const [y, m, d] = iso.split('-').map(Number);
  return Math.round((Date.UTC(y, m - 1, d) - Date.UTC(1899, 11, 30)) / 86400000);
}

const LAST_ROW = 60;
const LAST_COL = 11;

function makeSheet(name, cells, usedRows) {
  return {
    Name: name,
    UsedRange: { Row: 1, Column: 1, Rows: { Count: usedRows || LAST_ROW }, Columns: { Count: LAST_COL } },
    Cells(r, c) {
      const v = cells[r] && cells[r][c];
      return {
        get Text() { return (v && v.text !== undefined) ? v.text : ''; },
        get Value2() { return (v && v.value2 !== undefined) ? v.value2 : null; }
      };
    }
  };
}

// Application.Sheets 既要能当集合（.Count / .Item(i)），又要能当函数（Sheets("名")）
function makeApp(namedCells, activeName, usedRows) {
  const names = Object.keys(namedCells);
  const sheets = {};
  names.forEach(n => { sheets[n] = makeSheet(n, namedCells[n], usedRows); });
  const fn = function (n) {
    if (!sheets[n]) throw new Error('no such sheet: ' + n);
    return sheets[n];
  };
  fn.Count = names.length;
  fn.Item = (i) => sheets[names[i - 1]];
  return { ActiveSheet: sheets[activeName], Sheets: fn };
}

// ---------------------------------------------------------------- 测试数据

const s2 = {};
// 第 3 行表头、第 4 行示例行 —— 2026-10-04 在真实文档上确认的结构
s2[3] = { 1: { text: '发布日期' }, 2: { text: '发布单位' }, 3: { text: '文章标题' }, 4: { text: '是否头条' }, 10: { text: '备注' } };
// 以下全部为虚构示例数据，不含真实部门名或稿件标题。字符类别（丨/｜/|、####）刻意保留，
// 因为断言验证的是这些码位是否被逐字保留，与真实内容无关。
s2[4] = { 1: { text: '例：2024/12/5' }, 2: { text: '示例宣传组' }, 3: { text: '示例书院召开示例班…' } };
// 日期块一：6..13 合并，仅 A6 有值。日期用真实格式 2026/9/21
s2[6] = { 1: { text: '2026/9/21', value2: serial('2026-09-21') },
          2: { text: '示例体育部' },
          3: { text: '示例体育丨2026示例招募，示例队等你来！' },
          5: { text: '是' } };
s2[7] = { 2: { text: '示例实践组' }, 3: { text: '示例总结会预告推' }, 4: { text: '是' } };
s2[8] = { 2: { text: '示例生权部' }, 3: { text: '示例生活丨示例第二周' } };
// 日期块二：A38 的 .Text 因列宽过窄返回 '####'，靠 Value2 兜底
s2[38] = { 1: { text: '####', value2: serial('2026-09-28') },
           2: { text: '示例文艺部' }, 3: { text: '示例沙龙｜“示例甲|示例乙”' }, 10: { text: '待定' } };
s2[39] = { 2: { text: '示例学习部' }, 3: { text: '示例学习｜示例第三周周报—每周一练' } };

const FOUR_WEEKS = body => ({ 第一周: {}, 第二周: body, 第三周: {}, 第四周: {} });
const appMain = makeApp(FOUR_WEEKS(s2), '第二周');

// ---------------------------------------------------------------- 断言工具
let pass = 0, fail = 0;
function check(label, cond, extra) {
  if (cond) { pass++; console.log('  ok   ' + label); }
  else { fail++; console.log('  FAIL ' + label + (extra !== undefined ? ' → ' + JSON.stringify(extra) : '')); }
}

const logs = [];
const fakeConsole = { log: (...a) => logs.push(a.join(' ')) };
const call = (app, ctx) => JSON.parse(run(app, ctx, fakeConsole));

console.log('\n[1] mode=dump，显式指定 sheet=第二周');
let out = call(appMain, { argv: { mode: 'dump', sheet: '第二周' }, sheet_name: '第二周' });
check('ok=true', out.ok === true, out);
check('read_only 标记存在', out.read_only === true);
check('工作表名正确', out.sheet === '第二周', out.sheet);
check('枚举到 4 个工作表', out.sheet_names.length === 4, out.sheet_names);
check('scanned_to 落在 UsedRange 内', out.used_range.scanned_to === 60, out.used_range);
check('3 个块 / 其中 2 个日期块（表头不计入）', out.block_count === 3 && out.date_block_count === 2, out.blocks.map(b => b.kind));
check('第 3 行表头块 kind=text 且 date 为空', out.blocks[0].kind === 'text' && out.blocks[0].date === '', out.blocks[0]);
check('表头块不误报头条行/备注', out.blocks[0].is_headline_row === null && out.blocks[0].remarks_present === null, out.blocks[0]);
check('第 4 行示例行被排除，未成为日期块', out.example_rows.length === 1 && out.example_rows[0].row === 4, out.example_rows);
check('示例行 is_example=true', out.rows.find(r => r.row === 4).is_example === true);
check('示例行里的 2024/12/5 未产生伪日期块', !out.date_blocks.some(b => b.date === '2024-12-05'), out.date_blocks.map(b => b.date));
check('真实日期格式 2026/9/21 解析为 2026-09-21', out.date_blocks[0].date_raw === '2026/9/21' && out.date_blocks[0].date === '2026-09-21', out.date_blocks[0]);
check('日期块1 起始行 6、内容 3 行', out.date_blocks[0].start_row === 6 && out.date_blocks[0].row_count === 3, out.date_blocks[0]);
check('日期块1 结束行 = 8（9-13 行无内容）', out.date_blocks[0].end_row === 8, out.date_blocks[0]);
check('块1 归一化日期正确', out.date_blocks[0].date === '2026-09-21', out.date_blocks[0]);
check('块1 头条行 = 7', out.date_blocks[0].is_headline_row === 7, out.date_blocks[0]);
check('块2 备注非空被标出', out.date_blocks[1].remarks_present === true, out.date_blocks[1]);
check('块2 用 Value2 兜底出日期（Text 是 ####）', out.date_blocks[1].date === '2026-09-28', out.date_blocks[1]);
check('块2 的 date_raw 保留 #### 原文', out.date_blocks[1].date_raw === '####', out.date_blocks[1]);

const r6 = out.rows.find(r => r.row === 6);
const r7 = out.rows.find(r => r.row === 7);
const r8 = out.rows.find(r => r.row === 8);
check('第6行标题逐码位保留（含 U+4E28 丨）', r6.c3 === '示例体育丨2026示例招募，示例队等你来！', r6.c3);
check('含半角竖线的标题未被改写', out.rows.find(r => r.row === 38).c3 === '示例沙龙｜“示例甲|示例乙”');
check('合并单元格第7行 A 列原始为空', r7.c1 === '', r7.c1);
check('第7行前向填充到 2026-09-21', r7.date === '2026-09-21', r7.date);
check('第7行 block_start=false', r7.block_start === false);
check('第6行 block_start=true', r6.block_start === true);
check('第8行也前向填充', r8.date === '2026-09-21', r8.date);
check('头条列 "是" 保留', r7.c4 === '是', r7.c4);
check('a_value2_iso 与 date 一致', r6.a_value2_iso === '2026-09-21', r6.a_value2_iso);
check('空白行被跳过（5、9-13 行）', out.rows.every(r => [5, 9, 10, 11, 12, 13].indexOf(r.row) === -1), out.rows.map(r => r.row));
check('表头行 3、4 仍如实保留在 rows 里', [3, 4].every(n => out.rows.some(r => r.row === n)), out.rows.map(r => r.row));

console.log('\n[2] mode=day 只取当天块');
out = call(appMain, { argv: { mode: 'day', date: '2026-09-21' }, sheet_name: '第二周' });
check('day_found=true', out.day_found === true);
check('命中日期块', out.day !== null && out.day.block.date === '2026-09-21', out.day && out.day.block);
check('当天 3 行篇目', out.day.rows.length === 3, out.day.rows.map(r => r.row));
check('行序保持原表顺序 6,7,8', JSON.stringify(out.day.rows.map(r => r.row)) === '[6,7,8]');
const miss = call(appMain, { argv: { mode: 'day', date: '2026-01-01' }, sheet_name: '第二周' });
check('查无此日 → day_found=false、day=null（不静默）', miss.day_found === false && miss.day === null, miss.day);
check('查无此日时 date_blocks 仍完整返回', miss.date_block_count === 2, miss.date_block_count);

console.log('\n[3] A 列只写 "9月21日" 时的年份补全');
const appB = makeApp(FOUR_WEEKS({ 6: { 1: { text: '9月21日' }, 3: { text: '甲' } }, 7: { 3: { text: '乙' } } }), '第二周');
out = call(appB, { argv: { mode: 'dump', year: '2026' }, sheet_name: '第二周' });
check('date_raw 原样是 9月21日', out.date_blocks[0].date_raw === '9月21日', out.date_blocks[0]);
check('补全为 2026-09-21', out.date_blocks[0].date === '2026-09-21', out.date_blocks[0]);
check('无 Value2 时不误填 a_value2_iso', out.rows[0].a_value2_iso === undefined, out.rows[0]);

console.log('\n[4] 认不出的日期格式 → date 留空，不猜测');
const appC = makeApp(FOUR_WEEKS({ 6: { 1: { text: '第三周周三' }, 3: { text: '丙' } } }), '第二周');
out = call(appC, { argv: { mode: 'dump' }, sheet_name: '第二周' });
check('归为 kind=text', out.blocks[0].kind === 'text', out.blocks[0]);
check('date 为空而不是乱猜', out.date_block_count === 0 && out.blocks[0].date === '', out.blocks[0]);
check('date_raw 原样保留', out.blocks[0].date_raw === '第三周周三', out.blocks[0]);
check('该行内容仍如实导出', out.rows.find(r => r.row === 6).c3 === '丙', out.rows);

console.log('\n[5] 工作表定位');
out = call(appMain, { argv: { sheet: '第九周' }, sheet_name: '' });
check('不存在的显式工作表名 → 失败，不读其他表', out.ok === false && out.error === 'sheet_not_found', out);
out = call(appMain, { argv: {}, sheet_name: '第三周' });
check('Context.sheet_name 生效', out.sheet === '第三周', out.sheet);
check('argv.sheet 优先于 Context.sheet_name', call(appMain, { argv: { sheet: '第四周' }, sheet_name: '第三周' }).sheet === '第四周');

console.log('\n[6] 无 Context（在编辑器里手动运行）');
out = call(appMain, undefined);
check('不抛异常且 sheet 正确', out.ok === true && out.sheet === '第二周', out.sheet);
check('console.log 有概况输出', logs.some(l => l.indexOf('sheet=第二周') === 0), logs.slice(0, 2));

console.log('\n[7] max_rows 上限生效');
const bigCells = Object.assign({}, s2, { 150: { 3: { text: '超出上限的行' } } });
const appD = makeApp(FOUR_WEEKS(bigCells), '第二周', 300);
out = call(appD, { argv: { max_rows: 100 }, sheet_name: '第二周' });
check('capped=true', out.used_range.capped === true, out.used_range);
check('scanned_to=100', out.used_range.scanned_to === 100, out.used_range);
check('第150行未被读入', out.rows.every(r => r.row !== 150));

console.log('\n[8] 只读性：源码里不得出现任何赋值写入');const writes = SRC.split('\n')
  .map((line, i) => ({ line: line.trim(), no: i + 1 }))
  .filter(o => /\.(Value2|Formula|Text|Interior|Font)\s*=[^=]/.test(o.line) ||
               /\.Name\s*=\s*['"]/.test(o.line) ||
               /Sheets\.(Add|Delete)/.test(o.line));
check('没有对单元格/工作表/工作簿的写入调用', writes.length === 0, writes);

console.log('\n[9] 示例行写法变体都不得产生日期块');
['例：2024/12/5', '例如: 2025/1/3', '示例 2026/2/4', '样例：2026/3/5', '举例2026/4/6'].forEach(t => {
  const app = makeApp(FOUR_WEEKS({ 4: { 1: { text: t } }, 6: { 1: { text: '2026/9/21' }, 3: { text: '甲' } } }), '第二周');
  const o = call(app, { argv: { mode: 'dump' }, sheet_name: '第二周' });
  check('「' + t + '」不算日期块', o.date_block_count === 1 && o.date_blocks[0].date === '2026-09-21', o.date_blocks.map(b => b.date));
});

console.log('\n[10] 读取失败、头条冲突和审核列');
const faulty = makeApp(FOUR_WEEKS(s2), '第二周');
const faultySheet = faulty.ActiveSheet;
const originalCells = faultySheet.Cells;
faultySheet.Cells = (r,c) => {
  if (r === 7 && c === 3) return { get Text() { throw new Error('模拟读取失败'); }, Value2: null };
  return originalCells(r,c);
};
out = call(faulty, { argv: { sheet: '第二周' } });
check('单元格读取失败返回 ok=false', out.ok === false && out.scan_complete === false);
check('失败位置被明确保留', out.read_errors.some(e => e.row === 7 && e.col === 3 && e.field === 'Text'));
const badRange = makeApp(FOUR_WEEKS(s2), '第二周');
Object.defineProperty(badRange.ActiveSheet, 'UsedRange', { get() { throw new Error('模拟选区失败'); } });
out = call(badRange, { argv: { sheet: '第二周' } });
check('UsedRange 失败不得回落到仅读第一行', out.ok === false && out.error === 'used_range_read_failed');
const noHeadline = JSON.parse(JSON.stringify(s2));
noHeadline[6][4] = { text: '否' };
noHeadline[6][6] = { text: '否' };
noHeadline[6][7] = { text: '待审核' };
noHeadline[6][8] = { text: '示例意见' };
noHeadline[6][9] = { text: '暂不发布' };
out = call(makeApp(FOUR_WEEKS(noHeadline), '第二周'), { argv: {} });
check('否不算头条，保留真正的是', out.date_blocks[0].is_headline_row === 7);
check('默认读取所有 A:K 列', JSON.stringify(out.cols_read) === '[1,2,3,4,5,6,7,8,9,10,11]');
check('审核及发布列原文保留', out.rows.find(r => r.row === 6).c7 === '待审核' && out.rows.find(r => r.row === 6).c9 === '暂不发布');
noHeadline[6][4] = { text: '是' };
out = call(makeApp(FOUR_WEEKS(noHeadline), '第二周'), { argv: {} });
check('两个头条保留冲突，不偷偷选第一个', out.date_blocks[0].is_headline_row === null && out.date_blocks[0].headline_rows.length === 2);
noHeadline[6][4] = { text: '待定' };
out = call(makeApp(FOUR_WEEKS(noHeadline), '第二周'), { argv: {} });
check('头条未知值保留且不自动排序', out.date_blocks[0].is_headline_row === null && out.date_blocks[0].unknown_headline_rows[0] === 6);
out = call(appD, { argv: { max_rows: 100 }, sheet_name: '第二周' });
check('扫描被截断不能标记完整', out.scan_complete === false);

console.log('\n----------------------------------------');
console.log('通过 ' + pass + ' 项，失败 ' + fail + ' 项');
process.exit(fail ? 1 : 0);
