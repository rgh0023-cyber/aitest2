import unittest, tempfile, uuid
from pathlib import Path
from document_workflow import Journal
from submission_service import submit
from submission_contract import PROJECT_ID, SubmissionFailure
from test_workflow import FakeService


class SubmissionServiceTests(unittest.TestCase):
    def test_repeat_request_and_rebuilt_submission_cache_do_not_duplicate(self):
        with tempfile.TemporaryDirectory() as folder:
            journal=Journal(str(Path(folder)/'test.sqlite3'));service=FakeService()
            service.read=lambda receipt:[service.content[receipt['docKey']]]
            payload={'schema_version':1,'request_id':str(uuid.uuid4()),'project_id':PROJECT_ID,
                'module':'测试','title':'可删除测试','kind':'修正日志','reason':'测试','content':'正文'}
            principal={'subject_id':'agent-1','scope':'proposal:submit','projects':[PROJECT_ID]}
            first=submit(journal,service,payload,principal)
            self.assertEqual(submit(journal,service,payload,principal),first)
            import sqlite3
            with sqlite3.connect(journal.path) as db:db.execute('DELETE FROM submissions')
            db.close()
            self.assertEqual(submit(journal,service,payload,principal),first)
            self.assertEqual(len(service.created),1)
            with self.assertRaises(SubmissionFailure):submit(journal,service,dict(payload,content='changed'),principal)


if __name__=='__main__':unittest.main()
