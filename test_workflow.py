import tempfile
import unittest
import time
from unittest.mock import patch
from pathlib import Path
from document_workflow import Journal, WorkflowFailure, archive, publish, DocumentService
from proposal import create_proposal


class FakeService:
    operator = 'owner'
    def __init__(self):
        self.created = []
        self.content = {}
    def create(self, title, folder):
        self.created.append((title, folder))
        key = str(len(self.created))
        return {'docKey': key, 'nodeId': key, 'url': 'https://alidocs.dingtalk.com/i/nodes/' + key}
    def ensure_content(self, receipt, text):
        key = receipt['docKey']
        if key in self.content and self.content[key] != text:
            raise WorkflowFailure('mismatch')
        self.content[key] = text


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.journal = Journal(str(Path(self.temp.name) / 'test.sqlite3'))
        self.service = FakeService()
        self.proposal = create_proposal('level tunning', '模块', '版本一', '正式文档新版本', '原因', '正文')
        self.identity = {'union_id': 'owner', 'expires_at': time.time() + 600}
        self.approval = {'proposal_hash': self.proposal['sha256'], 'reviewer': 'owner', 'approved_at': 'time'}
    def tearDown(self):
        self.temp.cleanup()
    def test_separate_records_and_retry_does_not_create_duplicates(self):
        receipt = archive(self.journal, self.service, self.proposal)
        first = publish(self.journal, self.service, self.proposal, self.identity, self.approval, receipt)
        second = publish(self.journal, self.service, self.proposal, self.identity, self.approval, receipt)
        self.assertEqual(first, second)
        self.assertEqual(len(self.service.created), 4)
        self.assertEqual(len({r['docKey'] for r in first.values()}), 4)
    def test_expired_or_changed_approval_never_publishes(self):
        receipt = archive(self.journal, self.service, self.proposal)
        for identity, approval in [({'union_id': 'owner', 'expires_at': 0}, self.approval),
            (self.identity, dict(self.approval, proposal_hash='wrong'))]:
            with self.assertRaises(WorkflowFailure):
                publish(self.journal, self.service, self.proposal, identity, approval, receipt)
        self.assertEqual(len(self.service.created), 1)
    def test_unknown_creation_blocks_retry(self):
        with patch.object(self.service, 'create', side_effect=TimeoutError):
            with self.assertRaises(TimeoutError):
                archive(self.journal, self.service, self.proposal)
        with self.assertRaises(WorkflowFailure):
            archive(self.journal, self.service, self.proposal)
        self.assertEqual(len(self.service.created), 0)
    def test_reverification_rejects_remote_tampering(self):
        receipt = archive(self.journal, self.service, self.proposal)
        self.service.content[receipt['docKey']] = '改写'
        with self.assertRaises(WorkflowFailure):
            publish(self.journal, self.service, self.proposal, self.identity, self.approval, receipt)
        self.assertEqual(len(self.service.created), 1)
    def test_remote_partial_write_resumes_from_verified_prefix(self):
        service = object.__new__(DocumentService)
        service.headers = {}; service.operator = 'owner'
        text = '字' * 2000
        with patch.object(service, 'read', side_effect=[[text[:1800]], [text[:1800], text[1800:]]]), \
             patch('document_workflow.request_json', return_value={'success': True}) as request:
            service.ensure_content({'docKey': 'doc'}, text)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.args[2]['element']['paragraph']['text'], text[1800:])


if __name__ == '__main__':
    unittest.main()
