import time
import unittest
from unittest.mock import patch
from test_agent_access import MemoryService
from project_access import request_access, authorize, write
from document_workflow import WorkflowFailure
from login_flow import LoginRegistry, verify_login


class ProjectAccessTests(unittest.TestCase):
    def setUp(self):
        self.service = MemoryService()
        self.service.operator = 'owner'
        self.identity = {'union_id': 'participant', 'name': 'Test', 'expires_at': time.time()+1800}

    def test_verified_identity_requires_project_grant_and_revocation_is_immediate(self):
        request_access(self.service, self.identity)
        request_access(self.service, self.identity)
        self.assertEqual(len(self.service.nodes), 1)
        with self.assertRaises(WorkflowFailure):
            authorize(self.service, self.identity)
        write(self.service, self.identity, 'grant')
        self.assertEqual(authorize(self.service, self.identity)['subject_id'], 'DING-participant')
        write(self.service, self.identity, 'revoke')
        with self.assertRaises(WorkflowFailure):
            authorize(self.service, self.identity)

    def test_expired_identity_and_modified_grant_fail_closed(self):
        write(self.service, self.identity, 'grant')
        self.service.text['1'] = self.service.text['1'].replace('participant', 'intruder')
        with self.assertRaises(WorkflowFailure):
            authorize(self.service, self.identity)
        with self.assertRaises(WorkflowFailure):
            authorize(self.service, dict(self.identity, expires_at=0))

    @patch('login_flow.request_json')
    def test_submitter_login_accepts_nonowner_without_tokens_or_review_privileges(self, request):
        request.side_effect = [{'accessToken': 'USER_TOKEN'}, {'unionId': 'participant', 'nick': 'Test'}]
        identity = verify_login({'client_id':'c', 'client_secret':'s'}, 'code', 'submitter')
        self.assertEqual(identity['union_id'], 'participant')
        self.assertNotIn('USER_TOKEN', str(identity))
        self.assertNotIn('user_id', identity)
        registry = LoginRegistry()
        state, owner = registry.begin('submitter')
        self.assertEqual(registry.claim(state), 'submitter')


if __name__ == '__main__':
    unittest.main()
