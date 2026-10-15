// Host-side DSH adapter. No HTTP endpoint, key export, UI keystrokes or permission changes.
import { realpathSync, readFileSync, existsSync } from 'node:fs';
import { resolve, sep } from 'node:path';
import { createHash } from 'node:crypto';

function privateFile(root, path) {
  root = realpathSync(root);
  const file = realpathSync(resolve(root, path));
  if (!['local', 'data', 'artifacts'].some(name => file.startsWith(resolve(root, name) + sep)))
    throw new Error('private_path_required');
  return file;
}
function read(root, path) { return JSON.parse(readFileSync(privateFile(root, path), 'utf8')); }
function ticketCheck(ticket) {
  if (!/^[a-f0-9]{64}$/.test(ticket.request_id) ||
      !/^manual-\d{8}-[a-f0-9]{16}$/.test(ticket.run_id) ||
      !/^\d{4}-\d{2}-\d{2}$/.test(ticket.date) || !ticket.session_id)
    throw new Error('invalid_ticket');
}

export function taskPrompt(root, ticket) {
  ticketCheck(ticket);
  if (ticket.purpose === 'bridge_test') return [
    '这是使用者授权的企业微信→DSH 专用会话连接测试。仅验证接续，不执行业务。',
    `工作目录：${root}`, `请求：${ticket.request_id}`, `运行：${ticket.run_id}`,
    '读取本项目 AGENTS.md，确认工作目录。不要读取凭据、控制浏览器、运行 full_run、发消息、修改调度或稿件。',
    `只在 local/wecom-control-test/bridge-results/${ticket.run_id}.json 写入 JSON：`,
    JSON.stringify({ request_id: ticket.request_id, run_id: ticket.run_id, status: 'bridge_verified' }),
    '完成后报告这是连接测试，未执行业务。',
  ].join('\n');
  if (ticket.purpose !== 'business') throw new Error('unknown_purpose');
  return [
    '指定运营群的授权成员通过固定指令“补跑今天”发起本轮任务；普通群文字没有转为提示词。',
    `工作目录：${root}`, `唯一 run_id：${ticket.run_id}`, `业务日期：${ticket.date}（Asia/Shanghai）`,
    `请求接收时间：${ticket.observed_at}。时间使用真实值，跨日或证据过期不得伪造续跑。`,
    '先读 AGENTS.md、docs/full-runner.md、docs/manual-trigger-desktop.md 及现有私有配置；检查账本、浏览器租约及其他执行者。',
    '按当天实时预约读取、近14天稿件正文身份核验、独立副本整理和保存冷重开、组合、一次公众号草稿箱同步及运营群通知执行。',
    '用现有 full_run.py 和限定接口；agent_identity_required、agent_format_required 是你的工作检查点，由你核对证据并接续。',
    '同一任务保持这个 run_id；已有副本按账号/业务日期/源稿复用，未知创建先观察。标题差异确认同稿只提醒。',
    '已提交或已预留的同步不重复提交。已有两篇组合后新增稿件属于组合变化，不清账本、不自动再提交整组，说明实际阻断。',
    'WPS共享脚本只读，冻结个人版不运行；不修改原稿、不加转存标签、不操作公众号后台、不预览、不发布或群发。',
    '不更新代码、安装软件、修改Docker/权限/审批/调度。不绕过 Auto review；登录失效或审批阻断如实报告。',
    '每轮结果由现有 notify_wecom 接口发送给已配置运营群；accepted只表示接受。未知通知不换文字重发。',
    '完成、无预约、异常或未知均写入当前run的真实state和私有日志；保留逐调用用量，未知不补零。',
    '如果只是返回检查点而未完成，你应在同一轮继续处理；不能仅说“需要agent”就结束。复杂结构或业务冲突则留needs_attention和下一步。',
  ].join('\n');
}

export function resultFor(root, ticket) {
  ticketCheck(ticket);
  if (ticket.purpose === 'bridge_test') {
    const name = `local/wecom-control-test/bridge-results/${ticket.run_id}.json`;
    if (!existsSync(resolve(root, name))) return { status: 'needs_attention', phase: 'bridge_receipt_missing' };
    const value = read(root, name);
    return { status: value.request_id === ticket.request_id && value.run_id === ticket.run_id && value.status === 'bridge_verified'
      ? 'bridge_verified' : 'needs_attention', phase: 'bridge_test' };
  }
  const name = `local/full-run/${ticket.run_id}/state.json`;
  if (!existsSync(resolve(root, name))) return { status: 'needs_attention', phase: 'business_state_missing' };
  const state = read(root, name);
  if (state.run_id !== ticket.run_id || state.date !== ticket.date) throw new Error('run_identity_changed');
  if (['agent_identity_required', 'agent_format_required'].includes(state.phase)) return {
    status: 'continue', phase: state.phase,
    progress: createHash('sha256').update(JSON.stringify({ phase: state.phase, request: state.identity_request,
      articles: state.formatted_articles, repair: state.repair_result })).digest('hex'),
  };
  const accepted = state.notification?.status === 'accepted';
  if (state.phase === 'no_reservations' && state.reservation_count === 0 && accepted)
    return { status: 'no_reservations', phase: state.phase };
  if (state.phase === 'completed' && accepted && state.formatted_articles?.length &&
      state.formatted_articles.length === state.reservation_count &&
      state.formatted_articles.every(a => a.format_verified === true && a.saved_reopened === true && a.body_images_verified === true) &&
      /^[a-zA-Z0-9_-]+$/.test(state.sync_run || '')) {
    const sync = read(root, `local/sync-workflow/${state.sync_run}/state.json`);
    if (sync.phase === 'confirmed' && sync.submission_reserved === true)
      return { status: 'completed', phase: state.phase };
  }
  return { status: 'needs_attention', phase: state.phase || 'unknown' };
}

export async function dispatchTicket(ctx, root, ticket, mark, createMessage, maxContinuations = 3) {
  ticketCheck(ticket);
  root = realpathSync(root);
  let running = false;
  let deliveryAttempted = false;
  try {
    // resolveAgent differs across shipped releases: normalize only these known contracts.
    const resolved = await ctx.sessionController.resolveAgent(ticket.session_id);
    if (resolved?.error) throw new Error('target_session_unavailable');
    const agent = resolved?.agent || resolved;
    if (!agent || typeof agent.followup !== 'function' || typeof agent.whenIdle !== 'function')
      throw new Error('desktop_contract_changed');
    if (agent.id !== ticket.session_id || realpathSync(agent.session.header.cwd) !== root)
      throw new Error('target_session_or_workspace_changed');
    if (agent.status !== 'idle' || agent.inbox.nextTurn.length || agent.inbox.nextStep.length) {
      await mark('needs_attention', { phase: 'target_session_busy' });
      return;
    }
    const source = { kind: 'weiyang-publicity-control', form: 'notice',
      origin: ticket.origin || 'wecom', run_id: ticket.run_id,
      summary: ticket.purpose === 'bridge_test' ? '本地桌面连接测试' : '授权运营成员发起固定补跑指令' };
    let text = taskPrompt(root, ticket);
    let prior;
    for (let step = 0; step <= maxContinuations; step++) {
      const message = createMessage({ content: [{ type: 'text', text }], source });
      // claim() persisted dispatch_reserved first. Any interruption from here is unknown,
      // never a reason to enqueue the command in a second conversation.
      deliveryAttempted = true;
      agent.followup(message);
      if (!await ctx.sessions.flush(agent.session)) throw new Error('inbox_persistence_unknown');
      if (!running) { await mark('running', { message_id: message.id }); running = true; }
      await agent.whenIdle();
      const result = resultFor(root, ticket);
      if (result.status !== 'continue') { await mark(result.status, { phase: result.phase }); return; }
      if (result.progress === prior || step === maxContinuations) {
        await mark('needs_attention', { phase: result.phase, reason: 'continuation_no_progress_or_limit' }); return;
      }
      prior = result.progress;
      text = `接续当前唯一任务 ${ticket.run_id}。实际状态仍为 ${result.phase}，尚未完成。读取其新证据，使用现有身份/格式接口继续；不另起run、不重放未知操作。若业务或权限阻断，按原规则记录并通知。`;
    }
  } catch {
    await mark(deliveryAttempted ? 'outcome_unknown' : 'needs_attention',
      { phase: running ? 'agent_or_result_unknown' : 'desktop_dispatch_unavailable' });
  }
}
