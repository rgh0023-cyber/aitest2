import tempfile
import unittest
import time
from unittest.mock import patch
from pathlib import Path
from document_workflow import Journal, WorkflowFailure, archive, publish, DocumentService, FOLDERS, WORKSPACE
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

    def test_new_session_loads_exact_remote_proposal(self):
        archive(self.journal, self.service, self.proposal)
        reopened = Journal(self.journal.path)
        entries = reopened.proposals()
        self.assertEqual(entries[0]['status'], '待审核')
        self.service.read = lambda receipt: [self.service.content[receipt['docKey']]]
        self.assertEqual(reopened.load_proposal(self.service, entries[0]), self.proposal)
        self.service.content[entries[0]['receipt']['docKey']] += '篡改'
        with self.assertRaises(WorkflowFailure):
            reopened.load_proposal(self.service, entries[0])

    def test_published_proposal_cannot_load_as_pending(self):
        receipt = archive(self.journal, self.service, self.proposal)
        publish(self.journal, self.service, self.proposal, self.identity, self.approval, receipt)
        entry = self.journal.proposals()[0]
        self.assertEqual(entry['status'], '已发布')
        with self.assertRaises(WorkflowFailure):
            self.journal.load_proposal(self.service, entry)

    def recovery_service(self):
        service = self.service
        service.sign_key = b'test-only-signing-key'
        service.seal = lambda value: DocumentService.seal(service, value)
        service.recovery_record = lambda *args: DocumentService.recovery_record(service, *args)
        service.save_recovery_record = lambda *args: DocumentService.save_recovery_record(service, *args)
        service.read = lambda receipt: [service.content[receipt['docKey']]]
        service.list_nodes = lambda folder: [dict(nodeId=str(i+1), url='https://alidocs.dingtalk.com/i/nodes/'+str(i+1),
            name=name, recovery_folder=folder, workspaceId=WORKSPACE) for i,(name,target) in enumerate(service.created) if target==folder]
        return service

    def test_empty_database_restores_signed_pending_and_published(self):
        service = self.recovery_service()
        receipt = archive(self.journal, service, self.proposal)
        rebuilt = Journal(str(Path(self.temp.name) / 'rebuilt.sqlite3'))
        report = rebuilt.sync_and_restore(service)
        self.assertEqual(report['restored'], 1)
        entry = rebuilt.proposals()[0]
        self.assertEqual(entry['title'], self.proposal['title'])
        self.assertEqual(entry['module'], self.proposal['module'])
        self.assertEqual(rebuilt.load_proposal(service, entry), self.proposal)
        publish(self.journal, service, self.proposal, self.identity, self.approval, receipt)
        another = Journal(str(Path(self.temp.name) / 'another.sqlite3'))
        count = len(service.created)
        self.assertEqual(another.sync_and_restore(service)['restored'], 4)
        self.assertEqual(another.proposals()[0]['status'], '已发布')
        with self.assertRaises(WorkflowFailure):
            another.load_proposal(service, another.proposals()[0])
        self.assertEqual(another.load_proposal(service, another.proposals()[0], readonly=True), self.proposal)
        publish(another, service, self.proposal, self.identity, self.approval, receipt)
        self.assertEqual(len(service.created), count)

    def test_tampered_signature_blocks_orphan_retry(self):
        service = self.recovery_service()
        archive(self.journal, service, self.proposal)
        service.content['2'] = service.content['2'].replace('"signature":"', '"signature":"bad')
        rebuilt = Journal(str(Path(self.temp.name) / 'tampered.sqlite3'))
        report = rebuilt.sync_and_restore(service)
        self.assertTrue(report['warnings'])
        self.assertEqual(rebuilt.proposals(), [])
        with self.assertRaises(WorkflowFailure):
            archive(rebuilt, service, self.proposal)

    def test_legacy_local_receipts_are_migrated_before_rebuild(self):
        archive(self.journal, self.service, self.proposal)
        service = self.recovery_service()
        self.journal.sync_and_restore(service)
        rebuilt = Journal(str(Path(self.temp.name) / 'legacy-rebuild.sqlite3'))
        self.assertEqual(rebuilt.sync_and_restore(service)['restored'], 1)
        self.assertEqual(rebuilt.proposals()[0]['id'], self.proposal['id'])


if __name__ == '__main__':
    unittest.main()
