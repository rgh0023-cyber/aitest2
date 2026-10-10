import unittest
from unittest.mock import patch
from urllib.parse import urlparse, parse_qs
from login_flow import LoginRegistry, LoginFailure, authorization_url, verify_login, REDIRECT_URI, parse_callback
from dingtalk_client import ConnectionFailure


class LoginTests(unittest.TestCase):
    def test_callback_aliases_and_repeated_keys(self):
        for auth_codes, codes in [(['same'], []), ([], ['same']), (['same'], ['same'])]:
            self.assertEqual(parse_callback(['state'], auth_codes, codes, []), ('state', 'same', False))
        for states, auth_codes, codes, errors in [([], ['c'], [], []),
            (['s', 's'], ['c'], [], []), (['s'], ['c', 'c'], [], []),
            (['s'], ['c'], ['different'], []), (['s'], [], [], []),
            (['s'], ['c'], [], ['denied'])]:
            with self.assertRaises(LoginFailure):
                parse_callback(states, auth_codes, codes, errors)
        self.assertEqual(parse_callback(['s'], [], [], ['denied']), ('s', None, True))

    def test_single_use_and_origin_session_isolation(self):
        registry = LoginRegistry()
        state, owner = registry.begin()
        other_state, other_owner = registry.begin()
        with self.assertRaises(LoginFailure):
            registry.poll(state, other_owner)
        registry.claim(state)
        with self.assertRaises(LoginFailure):
            registry.claim(state)
        registry.complete(state, {'identity': 'verified'})
        self.assertEqual(registry.poll(state, owner), {'identity': 'verified'})
        with self.assertRaises(LoginFailure):
            registry.poll(state, owner)
        self.assertIsNone(registry.poll(other_state, other_owner))

    def test_expiry_cancellation_and_capacity(self):
        clock = [0]
        registry = LoginRegistry(clock=lambda: clock[0])
        state, owner = registry.begin()
        clock[0] = 301
        with self.assertRaises(LoginFailure):
            registry.claim(state)
        state, owner = registry.begin()
        registry.cancel(state, owner)
        with self.assertRaises(LoginFailure):
            registry.poll(state, owner)
        for _ in range(100):
            registry.begin()
        with self.assertRaises(LoginFailure):
            registry.begin()

    def test_login_url_contains_no_owner_secret_or_credentials(self):
        query = parse_qs(urlparse(authorization_url('client', 'state')).query)
        self.assertEqual(query['redirect_uri'], [REDIRECT_URI])
        self.assertEqual(query['scope'], ['openid'])
        self.assertEqual(query['state'], ['state'])
        self.assertNotIn('client_secret', query)
        self.assertNotIn('owner', query)

    @patch('login_flow.request_json')
    def test_verified_identity_has_no_tokens(self, request):
        request.side_effect = [{'accessToken': 'USER_TOKEN', 'refreshToken': 'REFRESH'},
            {'unionId': 'owner', 'nick': 'Name'}, {'accessToken': 'APP_TOKEN'},
            {'result': {'unionid': 'owner'}}]
        value = verify_login({'client_id': 'c', 'client_secret': 'SECRET', 'user_id': 'user'}, 'CODE')
        self.assertEqual(value['union_id'], 'owner')
        self.assertEqual(value['user_id'], 'user')
        self.assertGreater(value['expires_at'], value['authenticated_at'])
        for secret in ('USER_TOKEN', 'REFRESH', 'APP_TOKEN', 'SECRET', 'CODE'):
            self.assertNotIn(secret, str(value))

    @patch('login_flow.request_json')
    def test_different_user_cannot_become_reviewer(self, request):
        request.side_effect = [{'accessToken': 't'}, {'unionId': 'intruder'},
            {'accessToken': 'app'}, {'result': {'unionid': 'owner'}}]
        with self.assertRaises(ConnectionFailure) as error:
            verify_login({'client_id': 'c', 'client_secret': 's'}, 'code')
        self.assertEqual(error.exception.code, 'REVIEWER_NOT_ALLOWED')


if __name__ == '__main__':
    unittest.main()
