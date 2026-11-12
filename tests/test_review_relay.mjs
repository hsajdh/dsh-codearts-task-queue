import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { DatabaseSync } from 'node:sqlite';
import { ReviewRelay } from '../relay/index.js';

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), 'test-temp');
fs.mkdirSync(root, { recursive: true });

function fixture() {
  const directory = fs.mkdtempSync(path.join(root, 'review-'));
  const queuePath = path.join(directory, 'queue.sqlite');
  const statePath = path.join(directory, 'review.sqlite');
  const queue = new DatabaseSync(queuePath);
  queue.exec('CREATE TABLE tasks (id TEXT PRIMARY KEY,state TEXT,updated_at REAL)');
  const taskId = 'ca-0123456789abcdef';
  queue.prepare('INSERT INTO tasks VALUES (?,?,?)').run(taskId, 'queued', 1);
  const messages = [];
  const resumed = [];
  const agent = { session: { id: 'original-session', header: { cwd: directory, agentPreset: 'standard' } },
    followup: async message => { await new Promise(resolve => setImmediate(resolve)); messages.push(message); } };
  const ctx = { agents: { get: () => agent },
    sessionController: { agents: { resolveAgent: async id => { resumed.push(id); return { agent }; } } },
    logger: { warn: () => {} } };
  const config = { profileName: 'desktop', databasePath: queuePath, statePath };
  const relay = new ReviewRelay(ctx, config);
  relay.bind({ name: 'mcp__codearts_queue__submit_task', agent },
    { isError: false, content: [{ type: 'text', text: JSON.stringify({ id: taskId, state: 'queued' }) }] });
  return { directory, taskId, queue, messages, resumed, ctx, config, relay };
}

async function close(f, relays = [f.relay]) {
  for (const relay of relays) await relay.close();
  f.queue.close();
  assert.ok(path.resolve(f.directory).startsWith(path.resolve(root) + path.sep));
  fs.rmSync(f.directory, { recursive: true });
}

test('delivery wakes the original session once and does not auto-accept', async () => {
  const f = fixture();
  await f.relay.tick();
  assert.equal(f.messages.length, 0);
  f.queue.prepare('UPDATE tasks SET state=?,updated_at=?').run('awaiting_review', 2);
  await f.relay.tick();
  await f.relay.tick();
  assert.equal(f.messages.length, 1);
  assert.equal(f.messages[0].source.kind, 'codearts-task-delivery');
  assert.equal(f.messages[0].role, 'user');
  assert.match(f.messages[0].content[0].text, /queue_status/);
  assert.equal(f.queue.prepare('SELECT state FROM tasks').get().state, 'awaiting_review');
  await close(f);
});

test('blocked work notifies the parent once and keeps executor ownership', async () => {
  const f = fixture();
  f.queue.prepare('UPDATE tasks SET state=?,updated_at=?').run('needs_attention', 2);
  await f.relay.tick();
  await f.relay.tick();
  assert.equal(f.messages.length, 1);
  assert.match(f.messages[0].content[0].text, /needs_attention/);
  assert.match(f.messages[0].content[0].text, /awaiting_review/);
  assert.equal(f.queue.prepare('SELECT state FROM tasks').get().state, 'needs_attention');
  f.queue.prepare('UPDATE tasks SET state=?,updated_at=?').run('awaiting_review', 3);
  await f.relay.tick();
  assert.equal(f.messages.length, 2);
  await close(f);
});

test('blocked work canceled during parent restoration does not notify', async () => {
  const f = fixture();
  const originalAgent = f.ctx.agents.get();
  f.ctx.agents.get = () => undefined;
  f.ctx.sessionController.agents.resolveAgent = async () => {
    f.queue.prepare('UPDATE tasks SET state=?').run('canceled');
    return { agent: originalAgent };
  };
  f.queue.prepare('UPDATE tasks SET state=?,updated_at=?').run('needs_attention', 2);
  await f.relay.tick();
  assert.equal(f.messages.length, 0);
  await close(f);
});

test('two relay instances do not notify the same delivery concurrently', async () => {
  const f = fixture();
  const second = new ReviewRelay(f.ctx, f.config);
  f.queue.prepare('UPDATE tasks SET state=?,updated_at=?').run('awaiting_review', 2);
  await Promise.all([f.relay.tick(), second.tick()]);
  assert.equal(f.messages.length, 1);
  await close(f, [f.relay, second]);
});

test('restart resumes only the original parent and remembers delivery revision', async () => {
  const f = fixture();
  await f.relay.close();
  f.ctx.agents.get = () => undefined;
  const restarted = new ReviewRelay(f.ctx, f.config);
  f.queue.prepare('UPDATE tasks SET state=?,updated_at=?').run('awaiting_review', 3);
  await restarted.tick();
  assert.equal(f.resumed[0], 'original-session');
  await restarted.close();
  const again = new ReviewRelay(f.ctx, f.config);
  await again.tick();
  assert.equal(f.messages.length, 1);
  await close(f, [again]);
});

test('a revised delivery triggers another review notification', async () => {
  const f = fixture();
  f.queue.prepare('UPDATE tasks SET state=?,updated_at=?').run('awaiting_review', 2);
  await f.relay.tick();
  f.queue.prepare('UPDATE tasks SET updated_at=?').run(4);
  await f.relay.tick();
  assert.equal(f.messages.length, 2);
  assert.notEqual(f.messages[0].id, f.messages[1].id);
  await close(f);
});

test('idempotent resubmission keeps the original owner', async () => {
  const f = fixture();
  const other = { session: { id: 'another-session', header: { cwd: f.directory } } };
  f.relay.bind({ name: 'mcp__codearts_queue__submit_task', agent: other },
    { content: [{ type: 'text', text: JSON.stringify({ id: f.taskId, state: 'queued' }) }] });
  f.queue.prepare('UPDATE tasks SET state=?').run('awaiting_review');
  await f.relay.tick();
  assert.equal(f.relay.state.prepare('SELECT session_id FROM owners').get().session_id, 'original-session');
  await close(f);
});

test('malformed tool content leaves the existing ownership intact', async () => {
  const f = fixture();
  assert.doesNotThrow(() => f.relay.bind({ name: 'mcp__codearts_queue__submit_task', agent: f.ctx.agents.get() },
    { content: [{ type: 'text', text: 'Unexpected response' }] }));
  assert.equal(f.relay.state.prepare('SELECT count(*) AS total FROM owners').get().total, 1);
  await close(f);
});

test('session restoration errors back off and recover on a later attempt', async () => {
  const f = fixture();
  const originalAgent = f.ctx.agents.get();
  let attempts = 0;
  f.ctx.agents.get = () => undefined;
  f.ctx.sessionController.agents.resolveAgent = async () => {
    attempts++;
    return { error: new Error('Temporarily unavailable') };
  };
  f.queue.prepare('UPDATE tasks SET state=?,updated_at=?').run('awaiting_review', 2);
  await f.relay.tick();
  await f.relay.tick();
  assert.equal(attempts, 1);
  assert.equal(f.messages.length, 0);
  f.relay.state.prepare('UPDATE owners SET next_retry=0').run();
  f.ctx.sessionController.agents.resolveAgent = async () => ({ agent: originalAgent });
  await f.relay.tick();
  assert.equal(f.messages.length, 1);
  assert.equal(f.relay.state.prepare('SELECT error FROM owners').get().error, null);
  await close(f);
});

test('a delivery canceled during session restoration does not wake the model', async () => {
  const f = fixture();
  const originalAgent = f.ctx.agents.get();
  f.ctx.agents.get = () => undefined;
  f.ctx.sessionController.agents.resolveAgent = async () => {
    f.queue.prepare('UPDATE tasks SET state=?').run('canceled');
    return { agent: originalAgent };
  };
  f.queue.prepare('UPDATE tasks SET state=?,updated_at=?').run('awaiting_review', 2);
  await f.relay.tick();
  assert.equal(f.messages.length, 0);
  assert.equal(f.relay.state.prepare('SELECT notification_until FROM owners').get().notification_until, 0);
  await close(f);
});
