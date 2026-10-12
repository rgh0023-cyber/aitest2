import unittest
from unittest.mock import patch
from backup_service import run_backup, make_delta, restore_delta, decode, encode, LATEST_NAME
from document_workflow import WorkflowFailure, BACKUP_FOLDER


class Store:
    def __init__(self):
        self.data = {}; self.nodes = []; self.events = []
    def seal(self, value):
        import hashlib
        from proposal import canonical
        return hashlib.sha256(('test-key' + canonical(value)).encode()).hexdigest()
    def create(self, name, folder):
        key = str(len(self.nodes) + 1)
        node = {'name': name, 'nodeId': key, 'url': 'https://alidocs.dingtalk.com/i/nodes/' + key}
        self.nodes.append(node)
        return dict(node, docKey=key)
    def read(self, receipt):
        return [self.data[receipt['docKey']]]
    def ensure_content(self, receipt, text):
        self.events.append('save-diff-or-baseline')
        self.data[receipt['docKey']] = text
    def list_nodes(self, folder):
        return self.nodes
    def blocks_url(self, receipt):
        return 'test/' + receipt['docKey'] + '/blocks?operatorId=test'


class BackupTests(unittest.TestCase):
    def test_delta_restores_unicode_deleted_and_added_documents(self):
        old = {'项目': 'level tunning', 'removed': {'text': '旧\n末行'}}
        new = {'项目': 'level tunning', 'added': {'text': '新\n末行'}}
        diff = make_delta(old, new)
        self.assertEqual(restore_delta(diff, 1), old)
        self.assertEqual(restore_delta(diff, 2), new)
    def test_baseline_then_overwrite_after_verified_diff(self):
        store = Store()
        before = {'project': 'level tunning', 'documents': {'a': 'old'}}
        after = {'project': 'level tunning', 'documents': {'a': 'new'}}
        first = run_backup(store, before)
        self.assertIsNone(first['diff'])
        def overwrite(url, stage, body, headers):
            self.assertEqual(len(store.nodes), 2)
            self.assertIn('save-diff-or-baseline', store.events)
            store.data['1'] = body['content']
            return {'success': True}
        store.headers = {}
        with patch('backup_service.request_json', side_effect=overwrite):
            second = run_backup(store, after)
        self.assertEqual(first['latest']['url'], second['latest']['url'])
        self.assertEqual(len([n for n in store.nodes if n['name'] == LATEST_NAME]), 1)
        diff = decode(store, store.data['2'])['delta']
        self.assertEqual(restore_delta(diff, 1), before)
        self.assertEqual(restore_delta(diff, 2), after)
        self.assertEqual(run_backup(store, after)['status'], '源内容无变化')
    def test_tampered_baseline_never_overwrites(self):
        store = Store(); run_backup(store, {'project': 'level tunning', 'a': 1})
        store.data['1'] = 'tampered'
        with patch('backup_service.request_json') as request:
            with self.assertRaises(WorkflowFailure):
                run_backup(store, {'project': 'level tunning', 'a': 2})
        request.assert_not_called()
    def test_diff_failure_leaves_latest_unchanged(self):
        store = Store(); run_backup(store, {'project': 'level tunning', 'a': 1})
        original = store.data['1']
        with patch.object(store, 'ensure_content', side_effect=WorkflowFailure('write failed')):
            with self.assertRaises(WorkflowFailure):
                run_backup(store, {'project': 'level tunning', 'a': 2})
        self.assertEqual(store.data['1'], original)


if __name__ == '__main__':
    unittest.main()
