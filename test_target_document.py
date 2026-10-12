import unittest
from unittest.mock import patch
from target_document import resolve, PROJECT_ROOT
from document_workflow import WORKSPACE, WorkflowFailure


class Service:
    headers = {}
    operator = 'owner'
    def list_nodes(self, folder):
        return [{'nodeId':'target', 'workspaceId':WORKSPACE, 'type':'FILE', 'extension':'adoc'}]
    def blocks_url(self, receipt):
        return 'https://example.invalid/blocks'


class TargetTests(unittest.TestCase):
    @patch('target_document.request_json')
    def test_project_target_and_rich_baseline(self, request):
        request.return_value = {'success':True, 'result':{'data':[{'blockType':'table', 'table':{'rows':[]}}]}}
        result = resolve(Service(), 'https://alidocs.dingtalk.com/i/nodes/target')
        self.assertTrue(result['document_id'].startswith('DOC-'))
        self.assertEqual(len(result['content_sha256']),64)
        with self.assertRaises(WorkflowFailure):
            resolve(Service(), 'https://alidocs.dingtalk.com/i/nodes/outside')
        request.return_value['result']['hasMore'] = True
        with self.assertRaises(WorkflowFailure):
            resolve(Service(), result['url'])

    def test_arbitrary_url_and_folder_targets_are_denied(self):
        for url in ['http://localhost/secret', 'https://alidocs.dingtalk.com.evil/i/nodes/target',
                    'https://alidocs.dingtalk.com/i/nodes/'+PROJECT_ROOT]:
            with self.assertRaises(WorkflowFailure):
                resolve(Service(), url)


if __name__ == '__main__':
    unittest.main()
