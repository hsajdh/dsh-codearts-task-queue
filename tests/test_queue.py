from concurrent.futures import ThreadPoolExecutor
import importlib.util
from pathlib import Path
import tempfile
import unittest

SOURCE = Path(__file__).parent.parent / 'codearts_queue.py'
spec = importlib.util.spec_from_file_location('desktop_queue', SOURCE)
queue = importlib.util.module_from_spec(spec)
spec.loader.exec_module(queue)


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.test_root = Path(__file__).parent / 'test-temp'
        self.test_root.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=self.test_root)
        self.root = Path(self.temporary.name)
        queue.DATABASE = self.root / 'queue.sqlite'
        self.project = self.root / 'project'
        self.project.mkdir()

    def tearDown(self):
        if not self.root.resolve().is_relative_to(self.test_root.resolve()):
            raise RuntimeError('Test cleanup target escaped its workspace')
        self.temporary.cleanup()

    def submit(self, key='task-1'):
        return queue.submit_task(str(self.project), key, 'Test task', 'Create result.txt', ['result.txt exists'])

    def delivery(self, task, claimed):
        (self.project / 'result.txt').write_text('verified marker', encoding='utf-8')
        return queue.report_delivery(task['id'], claimed['task']['lease_token'], 'Created the file',
                                     ['result.txt'], ['Marker file written'])

    def test_submission_retry_reuses_task(self):
        first = self.submit()
        self.assertEqual(first['id'], self.submit()['id'])
        with self.assertRaises(ValueError):
            queue.submit_task(str(self.project), 'task-1', 'Changed task', 'Different work', ['check'])

    def test_concurrent_claims_have_one_owner(self):
        self.submit()
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda i: queue.claim_next('claim-' + str(i)), range(6)))
        self.assertEqual(sum(result['task'] is not None for result in results), 1)

    def test_claim_retry_reuses_same_ownership(self):
        self.submit()
        first = queue.claim_next('stable-claim')
        second = queue.claim_next('stable-claim')
        self.assertTrue(second['reused'])
        self.assertEqual(first['task']['lease_token'], second['task']['lease_token'])

    def test_wrong_owner_and_external_artifacts_are_rejected(self):
        task = self.submit()
        claimed = queue.claim_next('claim-1')
        with self.assertRaises(ValueError):
            queue.report_progress(task['id'], 'wrong-token', 'Working')
        (self.root / 'external.txt').write_text('outside project')
        with self.assertRaises(ValueError):
            queue.report_delivery(task['id'], claimed['task']['lease_token'], 'Done',
                                  [str(self.root / 'external.txt')], [])

    def test_delivery_requires_parent_review(self):
        task = self.submit()
        claimed = queue.claim_next('claim-1')
        self.assertEqual(self.delivery(task, claimed)['state'], 'awaiting_review')
        self.assertNotIn('lease_token', queue.queue_status(task['id']))
        with self.assertRaises(ValueError):
            queue.review_delivery(task['id'], True, '')
        self.assertEqual(queue.review_delivery(task['id'], True, 'Read and checked the file')['state'], 'accepted')

    def test_rejected_delivery_is_requeued_with_feedback(self):
        task = self.submit()
        claimed = queue.claim_next('claim-1')
        self.delivery(task, claimed)
        rejected = queue.review_delivery(task['id'], False, 'Fix the marker content')
        self.assertEqual(rejected['state'], 'queued')
        next_claim = queue.claim_next('claim-2')
        self.assertNotEqual(claimed['task']['lease_token'], next_claim['task']['lease_token'])
        self.assertEqual(next_claim['task']['review'], 'Fix the marker content')

    def test_expired_ownership_is_not_silently_reassigned(self):
        task = self.submit()
        queue.claim_next('claim-1')
        with queue.database() as connection:
            connection.execute('UPDATE tasks SET lease_until=1 WHERE id=?', (task['id'],))
        result = queue.claim_next('another-client')
        self.assertTrue(result['busy'])
        self.assertTrue(result['needs_attention'])

    def test_cancellation_takes_precedence_over_late_delivery(self):
        task = self.submit()
        claimed = queue.claim_next('claim-1')
        self.assertTrue(queue.cancel_task(task['id'])['cancel_requested'])
        self.assertEqual(self.delivery(task, claimed)['state'], 'canceled')
        self.assertEqual(self.submit()['state'], 'canceled')

    def test_delivery_retry_returns_existing_delivery(self):
        task = self.submit()
        claimed = queue.claim_next('claim-1')
        first = self.delivery(task, claimed)
        with queue.database() as connection:
            timestamp = connection.execute('SELECT updated_at FROM tasks WHERE id=?', (task['id'],)).fetchone()[0]
        repeated = self.delivery(task, claimed)
        self.assertEqual(repeated['state'], first['state'])
        self.assertTrue(repeated['reused'])
        with queue.database() as connection:
            self.assertEqual(connection.execute('SELECT updated_at FROM tasks WHERE id=?', (task['id'],)).fetchone()[0], timestamp)
        with self.assertRaises(ValueError):
            queue.report_delivery(task['id'], claimed['task']['lease_token'], 'Changed delivery', ['result.txt'], [])
        with self.assertRaises(ValueError):
            queue.report_progress(task['id'], claimed['task']['lease_token'], 'Late progress')
        with self.assertRaises(ValueError):
            queue.report_delivery(task['id'], 'wrong-token', 'Created the file', ['result.txt'], ['Marker file written'])

    def test_finished_pickup_key_cannot_claim_another_task(self):
        task = self.submit()
        claimed = queue.claim_next('one-pickup')
        self.delivery(task, claimed)
        second = self.submit('second-task')
        repeated = queue.claim_next('one-pickup')
        self.assertIsNone(repeated['task'])
        self.assertEqual(repeated['previous_task_id'], task['id'])
        self.assertEqual(queue.queue_status(second['id'])['state'], 'queued')
        queue.review_delivery(task['id'], True, 'Verified marker')
        self.assertIsNone(queue.claim_next('one-pickup')['task'])
        self.assertEqual(queue.claim_next('new-pickup')['task']['id'], second['id'])

    def test_old_pickup_key_cannot_obtain_a_new_executor_lease(self):
        task = self.submit()
        first = queue.claim_next('original-pickup')
        self.delivery(task, first)
        queue.review_delivery(task['id'], False, 'Please revise the marker')
        second = queue.claim_next('revision-pickup')
        repeated = queue.claim_next('original-pickup')
        self.assertIsNone(repeated['task'])
        self.assertNotIn(second['task']['lease_token'], str(repeated))

    def test_legacy_queue_without_claim_history_preserves_current_owner(self):
        task = self.submit()
        first = queue.claim_next('legacy-pickup')
        with queue.database() as connection:
            connection.execute('DROP TABLE claims')
        repeated = queue.claim_next('legacy-pickup')
        self.assertEqual(first['task']['lease_token'], repeated['task']['lease_token'])
        self.delivery(task, first)
        self.assertIsNone(queue.claim_next('legacy-pickup')['task'])

    def test_delivery_receipt_retry_survives_file_removal(self):
        task = self.submit()
        first = queue.claim_next('pickup')
        self.delivery(task, first)
        (self.project / 'result.txt').unlink()
        repeated = queue.report_delivery(task['id'], first['task']['lease_token'], 'Created the file',
                                         ['result.txt'], ['Marker file written'])
        self.assertTrue(repeated['reused'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
