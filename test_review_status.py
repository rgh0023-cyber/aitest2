import time,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from document_workflow import Journal, WorkflowFailure, archive, publish, reject
from proposal import create_proposal, fingerprint
from submission_status import lookup
import test_workflow as fixtures


class ReviewStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.journal=Journal(str(Path(self.temp.name)/'state.sqlite3'))
        self.service=fixtures.FakeService()
        fixtures.WorkflowTests.recovery_service(self)
        self.proposal=create_proposal('level tunning','测试','测试','正式文档新版本','原因','正文')
        self.proposal['submitted_by']='DING-submitter'
        self.proposal['sha256']=fingerprint(self.proposal)
        self.identity={'union_id':'owner','expires_at':time.time()+600}
        self.submitter={'union_id':'submitter','expires_at':time.time()+600}
        self.decision={'proposal_hash':self.proposal['sha256'],'reviewer':'owner','decision':'拒绝发布','approved_at':'test-time'}
        self.approval=dict(self.decision,decision='批准创建独立文档')
        self.receipt=archive(self.journal,self.service,self.proposal)
    def tearDown(self):
        self.temp.cleanup()

    def test_rejection_is_durable_idempotent_and_blocks_publication(self):
        first=reject(self.journal,self.service,self.proposal,self.identity,self.decision,self.receipt)
        count=len(self.service.created)
        self.assertEqual(reject(self.journal,self.service,self.proposal,self.identity,self.decision,self.receipt),first)
        self.assertEqual(len(self.service.created),count)
        rebuilt=Journal(str(Path(self.temp.name)/'rebuilt.sqlite3'))
        rebuilt.sync_and_restore(self.service)
        result=lookup(rebuilt,self.service,self.submitter,self.proposal['id'])
        self.assertEqual(result['status'],'已拒绝')
        self.assertFalse(result['is_formal'])
        with self.assertRaises(WorkflowFailure):
            publish(rebuilt,self.service,self.proposal,self.identity,self.approval,self.receipt)

    def test_published_formal_status_verifies_remote_records_and_private_identity(self):
        with self.assertRaises(WorkflowFailure):
            lookup(self.journal,self.service,dict(self.submitter,union_id='someone-else'),self.proposal['id'])
        result=lookup(self.journal,self.service,self.submitter,self.proposal['id'])
        self.assertEqual(result['status'],'待审核')
        receipts=publish(self.journal,self.service,self.proposal,self.identity,self.approval,self.receipt)
        result=lookup(self.journal,self.service,self.submitter,self.proposal['id'])
        self.assertTrue(result['is_formal'])
        self.assertEqual(result['formal_document_url'],receipts['published']['url'])
        self.service.content[receipts['published']['docKey']]='modified'
        with self.assertRaises(WorkflowFailure):
            lookup(self.journal,self.service,self.submitter,self.proposal['id'])

    def test_opposite_decision_and_expired_reviewer_are_denied(self):
        with self.assertRaises(WorkflowFailure):
            reject(self.journal,self.service,self.proposal,dict(self.identity,expires_at=0),self.decision,self.receipt)
        publish(self.journal,self.service,self.proposal,self.identity,self.approval,self.receipt)
        with self.assertRaises(WorkflowFailure):
            reject(self.journal,self.service,self.proposal,self.identity,self.decision,self.receipt)

    def test_failed_decision_never_reverts_to_pending_or_allows_opposite_action(self):
        with patch.object(self.service,'create',side_effect=TimeoutError):
            with self.assertRaises(TimeoutError):
                reject(self.journal,self.service,self.proposal,self.identity,self.decision,self.receipt)
        self.assertEqual(self.journal.proposals()[0]['status'],'审批执行中（需核对）')
        with self.assertRaises(WorkflowFailure):
            publish(self.journal,self.service,self.proposal,self.identity,self.approval,self.receipt)


if __name__=='__main__':
    unittest.main()
