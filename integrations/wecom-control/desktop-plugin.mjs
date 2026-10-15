import { Service } from '@deepseek-ai/cordis';
import { createUserMessage } from '@deepseek-ai/dsh-llm';
import z from '@deepseek-ai/schemastery';
import { DesktopWorker } from './desktop-worker.mjs';

export default class WeiyangDesktopControl {
  static inject = ['sessionController', 'sessions'];
  static Config = z.object({
    enabled: z.boolean().default(false).description('启用接续；先绑定私有测试配置').volatile(),
    projectRoot: z.string().default('').description('项目绝对路径').volatile(),
    configPath: z.string().default('local/wecom-control-test/config.json').description('项目内私有配置路径').volatile(),
    python: z.string().default('python3').description('本机 Python 解释器').volatile(),
  });
  constructor(ctx, config = {}) { this.worker = new DesktopWorker(ctx, config, createUserMessage); }
  async *[Service.init]() {
    const timer = setInterval(() => this.worker.pump(), 2000);
    yield () => { this.worker.stopped = true; clearInterval(timer); };
    this.worker.pump();
  }
}
