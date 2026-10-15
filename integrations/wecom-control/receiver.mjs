// Authorized Mac/Windows inbox. No arbitrary chat text reaches an agent or shell.
import AiBot from '@wecom/aibot-node-sdk';
import { spawn } from 'node:child_process';
import { readFileSync, mkdirSync, writeFileSync, openSync, closeSync, unlinkSync } from 'node:fs';
import { dirname, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const stateRoot = resolve(root, 'local/wecom-control-test');
const configPath = resolve(root, process.argv[2] || 'local/wecom-control-test/config.json');
if (!configPath.startsWith(resolve(root, 'local') + sep)) throw new Error('Use private local configuration');
const config = JSON.parse(readFileSync(configPath, 'utf8'));
if (!['queue_only', 'desktop'].includes(config.mode)) throw new Error('Unknown control mode');
const secret = process.env.WECOM_CONTROL_SECRET;
if (!config.bot_id || !secret) throw new Error('Bot ID and private WECOM_CONTROL_SECRET are required');
if (!config.allowed_chat_ids?.length || !config.allowed_user_ids?.length)
  throw new Error('Configure the approved group and member IDs before connecting');
mkdirSync(stateRoot, { recursive: true });
const lockPath = resolve(stateRoot, 'receiver.lock');
const lock = openSync(lockPath, 'wx', 0o600);
writeFileSync(lock, JSON.stringify({ pid: process.pid, started_at: new Date().toISOString() }));
process.on('exit', () => { closeSync(lock); unlinkSync(lockPath); });
function health(status) {
  writeFileSync(resolve(stateRoot, 'connection.json'), JSON.stringify({ status, observed_at: new Date().toISOString(), mode: config.mode }), { mode: 0o600 });
  console.log(JSON.stringify({ status, mode: config.mode }));
}
// SDK debug output can contain envelopes and credentials: never print its arguments.
const logger = { debug() {}, info() {}, warn() {}, error() { health('transport_error'); } };
const client = new AiBot.WSClient({ botId: config.bot_id, secret, logger, maxReconnectAttempts: -1 });
function route(body) {
  return new Promise((accept, reject) => {
    const child = spawn(process.env.WEIYANG_PYTHON || 'python3',
      [resolve(root, 'scripts/wecom_control.py'), '--config', configPath], { cwd: root, shell: false });
    let output = '';
    const timeout = setTimeout(() => { child.kill(); reject(new Error('policy_timeout')); }, 15000);
    child.stdout.on('data', data => { output += data; });
    child.stderr.resume(); // Do not expose configuration contents in traceback/log output.
    child.on('error', () => { clearTimeout(timeout); reject(new Error('policy_unavailable')); });
    child.on('close', code => {
      clearTimeout(timeout);
      if (code !== 0) return reject(new Error('policy_failed'));
      try { accept(JSON.parse(output)); } catch { reject(new Error('policy_invalid_result')); }
    });
    child.stdin.on('error', () => {});
    child.stdin.end(JSON.stringify(body));
  });
}
let chain = Promise.resolve();
client.on('authenticated', () => health('authenticated'));
client.on('disconnected', () => health('disconnected'));
client.on('reconnecting', () => health('reconnecting'));
client.on('error', () => health('transport_error'));
client.on('message.text', frame => {
  chain = chain.then(async () => {
    const result = await route(frame.body);
    if (!result.reply) return;
    try {
      const receipt = await client.replyStream(frame, 'control-' + (result.request_id || Date.now()), result.reply, true);
      // A transport ACK is not proof a member has read the reply.
      health(receipt?.errcode === 0 ? 'reply_accepted' : 'reply_outcome_unknown');
    } catch { health('reply_outcome_unknown'); }
  }).catch(() => health('policy_failed'));
});
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => { client.disconnect(); health('stopped'); process.exit(0); });
health('connecting');
client.connect();
