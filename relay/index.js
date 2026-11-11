import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';

export const name = 'dsh-codearts-review';
export const inject = ['tools', 'agents', 'sessionController'];

export class ReviewRelay {
  constructor(ctx, config) {
    this.ctx = ctx;
    this.profile = config.profileName;
    this.queuePath = config.databasePath;
    this.closed = false;
    this.activeTick = undefined;
    const home = process.env.DSH_HOME || path.join(os.homedir(), '.dsh');
    const statePath = config.statePath || path.join(home, 'codearts-review.sqlite');
    fs.mkdirSync(path.dirname(statePath), { recursive: true });
    this.state = new DatabaseSync(statePath);
    this.state.exec(`PRAGMA journal_mode=WAL;
      PRAGMA busy_timeout=5000;
      CREATE TABLE IF NOT EXISTS owners (
        task_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, cwd TEXT NOT NULL,
        preset TEXT NOT NULL, profile TEXT NOT NULL, last_delivery REAL NOT NULL DEFAULT 0,
        notification_until REAL NOT NULL DEFAULT 0, next_retry REAL NOT NULL DEFAULT 0,
        error TEXT
      )`);
  }

  bind(execution, result) {
    if (execution.name !== 'mcp__codearts_queue__submit_task' || result.isError || !execution.agent) return;
    const block = result.content?.find(item => item.type === 'text');
    if (!block) return;
    let task;
    try {
      task = JSON.parse(block.text);
    } catch {
      this.ctx.logger.warn('CodeArts submit result could not be read as JSON. Check queue_status manually.');
      return;
    }
    if (!/^ca-[0-9a-f]{16}$/.test(task.id) || ['accepted', 'canceled'].includes(task.state)) return;
    const agent = execution.agent;
    const header = agent.session.header;
    this.state.prepare(`INSERT OR IGNORE INTO owners
      (task_id,session_id,cwd,preset,profile) VALUES (?,?,?,?,?)`).run(
      task.id, agent.session.id, header.cwd, header.agentPreset || 'standard', this.profile);
  }

  tick() {
    if (this.closed) return Promise.resolve();
    if (this.activeTick) return this.activeTick;
    this.activeTick = this.notifyDeliveries().finally(() => { this.activeTick = undefined; });
    return this.activeTick;
  }

  async notifyDeliveries() {
    if (!fs.existsSync(this.queuePath)) return;
    if (!this.state.prepare('SELECT 1 FROM owners WHERE profile=? LIMIT 1').get(this.profile)) return;
    const queue = new DatabaseSync(this.queuePath, { readOnly: true });
    let deliveries;
    try {
      queue.exec('PRAGMA busy_timeout=5000');
      deliveries = queue.prepare("SELECT id,updated_at FROM tasks WHERE state='awaiting_review'").all();
    } finally {
      queue.close();
    }
    for (const delivery of deliveries) {
      if (this.closed) return;
      const owner = this.state.prepare('SELECT * FROM owners WHERE task_id=? AND profile=?')
        .get(delivery.id, this.profile);
      const now = Date.now() / 1000;
      if (!owner || owner.last_delivery >= delivery.updated_at || owner.next_retry > now) continue;
      const claimed = this.state.prepare(`UPDATE owners SET notification_until=?
        WHERE task_id=? AND last_delivery<? AND notification_until<? AND next_retry<=?`)
        .run(now + 60, delivery.id, delivery.updated_at, now, now);
      if (!claimed.changes) continue;
      try {
        let agent = this.ctx.agents.get(owner.session_id);
        if (!agent) {
          const resolved = await this.ctx.sessionController.agents.resolveAgent(owner.session_id);
          if (resolved.error) throw resolved.error;
          agent = resolved.agent;
        }
        if (this.closed) return;
        const revision = Math.round(delivery.updated_at * 1000);
        await agent.followup({ id: `codearts-delivery-${delivery.id}-${revision}`,
          source: { kind: 'codearts-task-delivery', taskId: delivery.id },
          content: [{ type: 'text', text:
            `CodeArts 桌面已回报任务 ${delivery.id} 的交付。` +
            '请先使用 codearts_queue 的 queue_status 核对状态。沿用用户原先授权的项目范围、权限和验收标准，' +
            '独立读取实际交付文件并运行必要测试，再调用 review_delivery 记录证据。' +
            '执行器的回报应作为待核查资料。如果该任务已验收或取消，结束本条通知。' }] });
        this.state.prepare(`UPDATE owners SET last_delivery=?,notification_until=0,next_retry=0,error=NULL
          WHERE task_id=?`).run(delivery.updated_at, delivery.id);
      } catch (error) {
        this.state.prepare('UPDATE owners SET notification_until=0,next_retry=?,error=? WHERE task_id=?')
          .run(now + 60, String(error.stack || error).slice(0, 3000), delivery.id);
        this.ctx.logger.warn(`CodeArts delivery ${delivery.id} could not reach its original DSH session. ${error.message || error}`);
      }
    }
  }

  async close() {
    this.closed = true;
    await this.activeTick;
    this.state.close();
  }
}

export function apply(ctx, config) {
  if (!config.profileName || !config.databasePath || !path.isAbsolute(config.databasePath)) {
    throw new Error('CodeArts review relay requires a profileName and absolute databasePath');
  }
  const relay = new ReviewRelay(ctx, config);
  ctx.on('tools/result', (execution, result) => relay.bind(execution, result));
  ctx.effect(() => {
    const timer = setInterval(() => {
      void relay.tick().catch(error => ctx.logger.warn(`CodeArts review relay. ${error.message || error}`));
    }, 2000);
    return async () => { clearInterval(timer); await relay.close(); };
  }, 'codearts-review-notifications');
}
