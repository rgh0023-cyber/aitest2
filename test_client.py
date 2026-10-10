import unittest
from unittest.mock import patch
from dingtalk_client import ConnectionFailure, test_connection


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.config = {'client_id': 'test', 'client_secret': 'SECRET', 'workspace_id': 'target', 'user_id': 'user'}

    @patch('dingtalk_client.request_json')
    def test_read_only_and_no_token_in_result(self, request):
        request.side_effect = [{'accessToken': 'PRIVATE_TOKEN'}, {'result': {'unionid': 'operator'}},
            {'workspace': {'workspaceId': 'target', 'name': 'Library', 'permissionRole': 'OWNER'}}]
        result = test_connection(self.config)
        self.assertNotIn('PRIVATE_TOKEN', str(result))
        self.assertNotIn('SECRET', str(result))
        self.assertEqual(len(request.call_args_list), 3)
        self.assertIn('workspaces/target?', request.call_args_list[2].args[0])
        self.assertNotIn('body', request.call_args_list[2].kwargs)

    @patch('dingtalk_client.request_json')
    def test_wrong_workspace_is_failure(self, request):
        request.side_effect = [{'accessToken': 'token'}, {'result': {'unionid': 'operator'}},
            {'workspace': {'workspaceId': 'wrong'}}]
        with self.assertRaises(ConnectionFailure) as error:
            test_connection(self.config)
        self.assertEqual(error.exception.code, 'WORKSPACE_MISMATCH')

    @patch('dingtalk_client.request_json', return_value={})
    def test_missing_token_stops_before_any_document_access(self, request):
        with self.assertRaises(ConnectionFailure):
            test_connection(self.config)
        self.assertEqual(request.call_count, 1)


if __name__ == '__main__':
    unittest.main()
