"""Read-only application diagnostics. Tokens stay in memory."""
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


class ConnectionFailure(Exception):
    def __init__(self, stage, code, scopes=()):
        self.stage = stage
        self.code = code if re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', str(code)) else 'API_ERROR'
        self.scopes = [s for s in scopes if isinstance(s, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', s)]
        super().__init__('DingTalk connection test failed')


def request_json(url, stage, body=None, headers=None):
    data = json.dumps(body).encode('utf-8') if body is not None else None
    req = Request(url, data=data, headers={'Content-Type': 'application/json', **(headers or {})})
    try:
        with urlopen(req, timeout=20) as response:
            result = json.load(response)
    except HTTPError as exc:
        try:
            error = json.load(exc)
        except (ValueError, OSError):
            error = {}
        raise ConnectionFailure(stage, error.get('code', 'HTTP_' + str(exc.code)),
            error.get('accessdenieddetail', {}).get('requiredScopes', [])) from None
    except (URLError, TimeoutError, ValueError, OSError):
        raise ConnectionFailure(stage, 'NETWORK_OR_RESPONSE_ERROR') from None
    if not isinstance(result, dict):
        raise ConnectionFailure(stage, 'INVALID_RESPONSE')
    if result.get('errcode', 0) != 0:
        raise ConnectionFailure(stage, str(result['errcode']),
            result.get('accessdenieddetail', {}).get('requiredScopes', []))
    return result


def test_connection(config):
    """Use app credentials, then configured operator; never mutate documents."""
    auth = request_json('https://api.dingtalk.com/v1.0/oauth2/accessToken', '应用认证',
        {'appKey': config['client_id'], 'appSecret': config['client_secret']})
    token = auth.get('accessToken')
    if not isinstance(token, str) or not token:
        raise ConnectionFailure('应用认证', 'MISSING_TOKEN')
    user = request_json('https://oapi.dingtalk.com/topapi/v2/user/get?' + urlencode({'access_token': token}),
        '操作人身份', {'userid': config.get('user_id', '014542292620111416')})
    operator = user.get('result', {}).get('unionid')
    if not isinstance(operator, str) or not operator:
        raise ConnectionFailure('操作人身份', 'MISSING_UNION_ID')
    url = 'https://api.dingtalk.com/v2.0/wiki/workspaces/' + quote(config['workspace_id'], safe='')
    url += '?' + urlencode({'operatorId': operator, 'withPermissionRole': 'true'})
    result = request_json(url, '知识库读取', headers={'x-acs-dingtalk-access-token': token})
    space = result.get('workspace', {})
    if space.get('workspaceId') != config['workspace_id']:
        raise ConnectionFailure('知识库读取', 'WORKSPACE_MISMATCH')
    return {'应用认证': '通过', '操作人身份': '通过', '知识库读取': '通过',
            '知识库': space.get('name', ''), '操作权限': space.get('permissionRole', ''),
            '文档修改': '无（只读测试）'}
