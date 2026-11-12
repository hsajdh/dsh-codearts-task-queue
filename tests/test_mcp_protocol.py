"""Exercise both real stdio roles with a simulated local executor and a temporary queue."""
import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
import json
from pathlib import Path
import sys
import tempfile
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent


@asynccontextmanager
async def server(role, database):
    parameters = StdioServerParameters(command=sys.executable, args=[str(ROOT / 'codearts_queue.py'),
        '--role', role, '--database', str(database)], env={'PYTHONUTF8': '1'})
    async with stdio_client(parameters) as streams:
        async with ClientSession(*streams, read_timeout_seconds=timedelta(seconds=15)) as session:
            await session.initialize()
            yield session


async def call(session, name, arguments):
    result = await session.call_tool(name, arguments)
    if result.isError:
        raise AssertionError(result)
    return json.loads(next(block.text for block in result.content if block.type == 'text'))


class ProtocolTests(unittest.TestCase):
    def test_real_stdio_pickup_respects_workspace_scope(self):
        parent = ROOT / 'tests/test-temp'
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as temporary:
            directory = Path(temporary).resolve()
            workspace = directory / 'workspace'
            nested = workspace / 'nested'
            nested.mkdir(parents=True)
            foreign = directory / 'workspace-other'
            foreign.mkdir()
            database = directory / 'queue.sqlite'

            async def scenario():
                async with server('dispatcher', database) as dispatch, server('executor', database) as execute:
                    outside = await call(dispatch, 'submit_task', {'project_dir': str(foreign),
                        'request_key': 'outside', 'title': 'Outside', 'instructions': 'Other workspace', 'acceptance': ['check']})
                    wanted = await call(dispatch, 'submit_task', {'project_dir': str(nested),
                        'request_key': 'inside', 'title': 'Inside', 'instructions': 'Current workspace', 'acceptance': ['check']})
                    claim = await call(execute, 'claim_next', {'claim_key': 'scoped', 'project_dir': str(workspace)})
                    self.assertEqual(claim['task']['id'], wanted['id'])
                    self.assertEqual((await call(dispatch, 'queue_status', {'task_id': outside['id']}))['state'], 'queued')

            asyncio.run(asyncio.wait_for(scenario(), timeout=40))

    def test_real_stdio_handoff_and_review(self):
        parent = ROOT / 'tests/test-temp'
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as temporary:
            directory = Path(temporary).resolve()
            project = directory / 'project'
            project.mkdir()
            database = directory / 'queue.sqlite'

            async def scenario():
                async with server('dispatcher', database) as dispatch, server('executor', database) as execute:
                    self.assertEqual({tool.name for tool in (await dispatch.list_tools()).tools},
                                     {'submit_task', 'queue_status', 'review_delivery', 'cancel_task'})
                    executor_tools = (await execute.list_tools()).tools
                    self.assertEqual({tool.name for tool in executor_tools},
                                     {'claim_next', 'report_progress', 'report_delivery'})
                    claim_tool = next(tool for tool in executor_tools if tool.name == 'claim_next')
                    self.assertIn('native scheduled', claim_tool.description)
                    task = await call(dispatch, 'submit_task', {'project_dir': str(project),
                        'request_key': 'isolated-protocol', 'title': 'Protocol fixture',
                        'instructions': 'Create result.txt in this isolated fixture', 'acceptance': ['Exact marker']})
                    claim = await call(execute, 'claim_next', {'claim_key': 'one-manual-pickup'})
                    owner = {'task_id': task['id'], 'lease_token': claim['task']['lease_token']}
                    self.assertEqual(claim['task']['id'], task['id'])
                    await call(execute, 'report_progress', {**owner, 'summary': 'Simulating file delivery'})
                    artifact = project / 'result.txt'
                    artifact.write_text('PROTOCOL_FIXTURE_OK\n', encoding='utf-8')
                    delivery = {**owner, 'summary': 'Local test fixture created',
                        'artifacts': ['result.txt'], 'tests': ['Fixture writer completed']}
                    await call(execute, 'report_delivery', delivery)
                    self.assertTrue((await call(execute, 'report_delivery', delivery))['reused'])
                    state = await call(dispatch, 'queue_status', {'task_id': task['id']})
                    self.assertEqual(state['state'], 'awaiting_review')
                    self.assertNotIn('lease_token', state)
                    self.assertEqual(artifact.read_text(encoding='utf-8'), 'PROTOCOL_FIXTURE_OK\n')
                    accepted = await call(dispatch, 'review_delivery', {'task_id': task['id'],
                        'accepted': True, 'evidence': 'Independently read the fixture and verified the exact marker'})
                    self.assertEqual(accepted['state'], 'accepted')
                    second = await call(dispatch, 'submit_task', {'project_dir': str(project),
                        'request_key': 'next-protocol-task', 'title': 'Next fixture',
                        'instructions': 'Another bounded test fixture', 'acceptance': ['Explicit new pickup']})
                    repeated = await call(execute, 'claim_next', {'claim_key': 'one-manual-pickup'})
                    self.assertIsNone(repeated['task'])
                    self.assertEqual(repeated['previous_task_id'], task['id'])
                    self.assertEqual((await call(dispatch, 'queue_status', {'task_id': second['id']}))['state'], 'queued')
                    await call(dispatch, 'cancel_task', {'task_id': second['id']})

            asyncio.run(asyncio.wait_for(scenario(), timeout=40))
            self.assertTrue(directory.is_relative_to(parent.resolve()))


if __name__ == '__main__':
    unittest.main(verbosity=2)
