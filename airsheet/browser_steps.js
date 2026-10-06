// Load these function definitions into the agent's documented CUA JS session.
// Requires a tab bound through CUA and the expected shared day-reader source.
// This file is not a standalone Node/cron entry and does not acquire credentials.
async function startWpsDayRead(tab, source, requestedDate = "") {
  if (requestedDate && !/^\d{4}-\d{2}-\d{2}$/.test(requestedDate)) throw new Error("invalid_date");
  if (requestedDate) throw new Error("historical_parameter_ui_not_configured");
  if (!source.includes('const SCRIPT_VERSION = "1.2";')) throw new Error("wrong_shared_source");
  if (!(await tab.playwright.locator('iframe#excelIde').count())) {
    await tab.playwright.getByRole('button', {name:'效率', exact:true}).click();
    await tab.getAXState({emit:false});
    // During hydration WPS can reset the ribbon to Start after a successful click.
    // Observe that condition before one harmless reselection; never rerun a job.
    if (!(await tab.playwright.getByRole('button', {name:'高级开发', exact:true}).count())) {
      await tab.playwright.getByRole('button', {name:'效率', exact:true}).click();
      await tab.getAXState({emit:false});
    }
    await tab.playwright.getByRole('button', {name:'高级开发', exact:true})
      .waitFor({state:'visible', timeoutMs:30000});
    await tab.playwright.getByRole('button', {name:'高级开发', exact:true}).click();
    await tab.playwright.getByText('AirScript脚本编辑器', {exact:true}).click();
    await tab.getAXState({emit:false});
  }
  const frame = tab.playwright.frameLocator('iframe#excelIde');
  const shared = frame.locator('div.listItem').filter({
    has:frame.locator('div.header-title').filter({hasText:'文档共享脚本'})
  });
  const name = '未央预约表只读自动读取';
  if (!(await shared.getByText(name, {exact:true}).count())) {
    await shared.locator('span.title-content').filter({hasText:'文档共享脚本'}).click();
    await tab.getAXState({emit:false});
  }
  // Absence/login failure stops here; never create another script on retry.
  await shared.getByText(name, {exact:true}).waitFor({state:'visible', timeoutMs:30000});
  await shared.getByText(name, {exact:true}).click();
  const editor = frame.getByRole('textbox', {name:'编辑器内容;按 Alt+F1 可打开辅助功能选项。', exact:true});
  await editor.press('ControlOrMeta+A');
  await editor.press('ControlOrMeta+C');
  const current = await tab.clipboard.readText();
  const normalized = value=>value.replace(/\r\n/g,'\n').trimEnd();
  if (normalized(current)!==normalized(source)) throw new Error('shared_source_changed');
  // Select/copy verifies source only. Never paste, save or silently repair here.
  const clickedAt = Date.now();
  await frame.locator('button#run-script').click();
  await tab.getAXState({emit:false});
  return {clicked_at:clickedAt, requested_date:requestedDate};
}

async function collectWpsDayRead(tab, ticket) {
  const frame = tab.playwright.frameLocator('iframe#excelIde');
  await frame.locator('div.concent').filter({hasText:/^WY_END /})
    .waitFor({state:'visible', timeoutMs:45000});
  const lines = await frame.locator('div.concent').allTextContents({timeoutMs:10000});
  const begin = lines.filter(line=>line.startsWith('WY_BEGIN '));
  if (begin.length!==1) throw new Error('missing_or_multiple_run');
  const match=begin[0].match(/^WY_BEGIN (wps-(\d+)) /);
  if (!match || Number(match[2]) < ticket.clicked_at-5000 || Number(match[2])>Date.now()+5000) {
    throw new Error('old_or_unknown_execution');
  }
  if (!lines.includes('执行完毕')) throw new Error('execution_pending');
  // Keep the returned logs in private evidence; do not print to public logs.
  // Decode and validate them using read_wps_reservation.py before acting.
  return {run_id:match[1], logs:lines};
}
