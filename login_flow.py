"""Single-process OAuth rendezvous. Callback tabs never acquire a login session."""
import hmac
import secrets
import threading
import time
from urllib.parse import urlencode
from dingtalk_client import request_json, ConnectionFailure

REDIRECT_URI = 'https://aitest-kltimq4ak9guq9eijbhz3j.streamlit.app/'


class LoginFailure(Exception):
    pass


class LoginRegistry:
    def __init__(self, clock=time.monotonic, ttl=300):
        self.clock, self.ttl = clock, ttl
        self.lock = threading.Lock()
        self.items = {}

    def _prune(self):
        now = self.clock()
        self.items = {k: v for k, v in self.items.items() if v['expires'] > now}

    def begin(self):
        with self.lock:
            self._prune()
            if len(self.items) >= 100:
                raise LoginFailure('登录请求过多，请稍后再试。')
            state, owner = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            self.items[state] = {'owner': owner, 'expires': self.clock() + self.ttl,
                                 'status': 'pending', 'result': None}
            return state, owner

    def claim(self, state):
        with self.lock:
            self._prune()
            item = self.items.get(state)
            if not item or item['status'] != 'pending':
                raise LoginFailure('登录请求无效、已使用或已过期，请返回原页面重新发起。')
            item['status'] = 'processing'

    def complete(self, state, result):
        with self.lock:
            self._prune()
            item = self.items.get(state)
            if not item or item['status'] != 'processing':
                raise LoginFailure('登录请求已失效，请重新发起。')
            item['status'], item['result'] = 'complete', result

    def poll(self, state, owner):
        with self.lock:
            self._prune()
            item = self.items.get(state)
            if not item or not hmac.compare_digest(item['owner'], owner):
                raise LoginFailure('登录会话已失效，请重新发起。')
            if item['status'] != 'complete':
                return None
            result = item['result']
            del self.items[state]
            return result

    def cancel(self, state, owner):
        with self.lock:
            item = self.items.get(state)
            if item and hmac.compare_digest(item['owner'], owner):
                del self.items[state]


def authorization_url(client_id, state):
    return 'https://login.dingtalk.com/oauth2/auth?' + urlencode({
        'client_id': client_id, 'redirect_uri': REDIRECT_URI, 'state': state,
        'response_type': 'code', 'prompt': 'consent', 'scope': 'openid'})


def verify_login(config, code):
    """Read identity using user token, authorize by verified configured operator identity."""
    auth = request_json('https://api.dingtalk.com/v1.0/oauth2/userAccessToken', '登录授权码交换',
        {'clientId': config['client_id'], 'clientSecret': config['client_secret'],
         'code': code, 'grantType': 'authorization_code'})
    token = auth.get('accessToken')
    if not isinstance(token, str) or not token:
        raise ConnectionFailure('登录授权码交换', 'MISSING_TOKEN')
    user = request_json('https://api.dingtalk.com/v1.0/contact/users/me', '登录用户身份',
        headers={'x-acs-dingtalk-access-token': token})
    union_id = user.get('unionId')
    if not isinstance(union_id, str) or not union_id:
        raise ConnectionFailure('登录用户身份', 'MISSING_UNION_ID')
    app = request_json('https://api.dingtalk.com/v1.0/oauth2/accessToken', '审核人校验',
        {'appKey': config['client_id'], 'appSecret': config['client_secret']})
    app_token = app.get('accessToken')
    if not isinstance(app_token, str) or not app_token:
        raise ConnectionFailure('审核人校验', 'MISSING_TOKEN')
    expected = request_json('https://oapi.dingtalk.com/topapi/v2/user/get?' +
        urlencode({'access_token': app_token}), '审核人校验',
        {'userid': config.get('user_id', '014542292620111416')})
    expected_union = expected.get('result', {}).get('unionid')
    if not isinstance(expected_union, str) or not expected_union:
        raise ConnectionFailure('审核人校验', 'MISSING_UNION_ID')
    if not hmac.compare_digest(union_id.encode(), expected_union.encode()):
        raise ConnectionFailure('审核人校验', 'REVIEWER_NOT_ALLOWED')
    return {'union_id': union_id, 'name': user.get('nick') or '已认证审核人',
            'user_id': config.get('user_id', '014542292620111416'),
            'authenticated_at': time.time(), 'expires_at': time.time() + 1800}
