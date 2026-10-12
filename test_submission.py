import unittest
import uuid
from submission_contract import validate, SubmissionFailure, PROJECT_ID
from proposal import verify


class SubmissionContractTests(unittest.TestCase):
    def setUp(self):
        self.payload = {'schema_version':1,'request_id':str(uuid.uuid4()),'project_id':PROJECT_ID,
            'module':'测试','title':'可删除测试','kind':'修正日志','reason':'接口验证','content':'测试正文'}
        self.principal = {'subject_id':'participant-1','scope':'proposal:submit','projects':[PROJECT_ID]}
    def test_authenticated_identity_and_pending_status(self):
        result = validate(self.payload, self.principal)
        self.assertEqual(result['submitted_by'], 'participant-1')
        self.assertEqual(result['status'], '待审核')
        self.assertTrue(verify(result))
        self.assertEqual(validate(self.payload,self.principal)['id'],result['id'])
        self.assertNotEqual(validate(self.payload,dict(self.principal,subject_id='other'))['id'],result['id'])
    def test_caller_cannot_supply_approval_or_identity(self):
        for field in ('approved','approval','reviewer','submitted_by','workspace_id','folder_id','status'):
            with self.assertRaises(SubmissionFailure):
                validate(dict(self.payload, **{field:'spoofed'}), self.principal)
    def test_cross_project_and_scope_denied(self):
        for principal in [dict(self.principal,projects=[]),dict(self.principal,scope='publish')]:
            with self.assertRaises(SubmissionFailure):
                validate(self.payload, principal)
    def test_invalid_request_or_code_reference_denied(self):
        for altered in [dict(self.payload,request_id='invalid'),
            dict(self.payload,code_bindings=[{'repository_url':'https://example.com','path':'a.py','commit_sha':'main'}])]:
            with self.assertRaises(SubmissionFailure):
                validate(altered,self.principal)


if __name__ == '__main__':
    unittest.main()
