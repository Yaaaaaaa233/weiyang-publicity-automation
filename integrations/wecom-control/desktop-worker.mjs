import { readFileSync, realpathSync, writeFileSync, mkdirSync } from 'node:fs';
import { resolve, sep } from 'node:path';
import { spawn } from 'node:child_process';
import { dispatchTicket } from './desktop-bridge.mjs';

// Live settings are references in DSH; ordinary values remain useful in isolated tests.
const setting = value => typeof value?.get === 'function' ? value.get() : value;
export class DesktopWorker {
  constructor(ctx, config, createMessage) {
    this.ctx = ctx; this.config = config; this.createMessage = createMessage;
    this.stopped = false; this.running = null;
  }
  configure() {
    if (setting(this.config.enabled) !== true) return false;
    const root = realpathSync(setting(this.config.projectRoot));
    const path = realpathSync(resolve(root, setting(this.config.configPath) || 'local/wecom-control-test/config.json'));
    if (!path.startsWith(resolve(root, 'local') + sep)) throw new Error('private_config_required');
    const policy = JSON.parse(readFileSync(path, 'utf8'));
    if (policy.mode !== 'desktop' || !policy.desktop_session_id) throw new Error('desktop_not_configured');
    this.root = root; this.path = path; this.policy = policy;
    this.python = setting(this.config.python) || 'python3';
    const signature = JSON.stringify([root, path, policy.desktop_session_id, policy.purpose]);
    if (signature !== this.mountedSignature) {
      mkdirSync(resolve(root, 'local/wecom-control-test'), { recursive: true });
      writeFileSync(resolve(root, 'local/wecom-control-test/desktop.json'), JSON.stringify({
        status: 'mounted', observed_at: new Date().toISOString(), purpose: policy.purpose,
        session_id: policy.desktop_session_id,
      }), { mode: 0o600 });
      this.mountedSignature = signature;
    }
    return true;
  }
  call(op, body) {
    return new Promise((accept, reject) => {
      const child = spawn(this.python, [resolve(this.root, 'scripts/wecom_control.py'),
        '--config', this.path, '--op', op], { cwd: this.root, shell: false });
      let out = '';
      const timer = setTimeout(() => { child.kill(); reject(new Error('policy_timeout')); }, 15000);
      child.stdout.on('data', chunk => { out += chunk; });
      child.stderr.resume();
      child.on('error', () => { clearTimeout(timer); reject(new Error('policy_unavailable')); });
      child.on('close', code => { clearTimeout(timer); try { code === 0 ? accept(JSON.parse(out)) : reject(new Error('policy_failed')); } catch { reject(new Error('policy_output_invalid')); } });
      child.stdin.on('error', () => {});
      child.stdin.end(body ? JSON.stringify(body) : '');
    });
  }
  pump() {
    if (this.stopped || this.running) return;
    this.running = (async () => {
      if (!this.configure()) return;
      const ticket = await this.call('claim');
      if (ticket.status !== 'dispatch_reserved') return;
      await dispatchTicket(this.ctx, this.root, ticket,
        (status, detail) => this.call('mark', { request_id: ticket.request_id, status, detail }), this.createMessage);
    })().catch(() => {
      if (!this.lastErrorAt || Date.now() - this.lastErrorAt > 60000) {
        this.lastErrorAt = Date.now();
        this.ctx.logger.warn('weiyang control: dispatch stopped; inspect private journal, do not replay');
      }
    })
      .finally(() => { this.running = null; });
  }
}
