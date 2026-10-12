import unittest
from unittest.mock import patch
from daily_backup import configured_service
from document_workflow import WorkflowFailure


class ServiceConfigTests(unittest.TestCase):
    @patch('daily_backup.DocumentService')
    @patch('daily_backup.request_json')
    def test_missing_optional_operator_uses_same_pilot_default(self, request, service):
        request.side_effect=[{'accessToken':'TOKEN'}, {'result':{'unionid':'owner'}}]
        config={'client_id':'client','client_secret':'secret','workspace_id':'workspace'}
        self.assertIs(configured_service(config), service.return_value)
        self.assertEqual(request.call_args.args[2], {'userid':'014542292620111416'})
        self.assertEqual(service.call_args.args[1]['union_id'], 'owner')

    @patch('daily_backup.request_json')
    def test_blank_operator_stops_before_any_api_call(self, request):
        with self.assertRaises(WorkflowFailure):
            configured_service({'user_id':''})
        request.assert_not_called()


if __name__=='__main__':
    unittest.main()
