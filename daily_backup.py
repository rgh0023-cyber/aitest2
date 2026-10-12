"""Cloud entry point. Print only safe receipts; never raw API errors or credentials."""
import json
import os
import sys
import time
from urllib.parse import urlencode
from backup_service import run_backup
from document_workflow import DocumentService, WorkflowFailure, WORKSPACE
from dingtalk_client import request_json, ConnectionFailure


def configured_service(config):
    auth = request_json('https://api.dingtalk.com/v1.0/oauth2/accessToken', '定时备份应用认证',
        {'appKey': config['client_id'], 'appSecret': config['client_secret']})
    token = auth.get('accessToken')
    if not token:
        raise WorkflowFailure('备份无法获取应用凭据。')
    user = request_json('https://oapi.dingtalk.com/topapi/v2/user/get?' + urlencode({'access_token': token}),
        '定时备份操作人验证', {'userid': config['user_id']})
    operator = user.get('result', {}).get('unionid')
    if not operator:
        raise WorkflowFailure('备份操作人无法验证。')
    return DocumentService(config, {'union_id': operator, 'expires_at': time.time() + 3600})


def main():
    try:
        secret = os.environ.get('DINGTALK_CLIENT_SECRET')
        if not secret:
            raise WorkflowFailure('未配置 DINGTALK_CLIENT_SECRET。')
        config = {'client_id': 'dingetvo8s9e2oxudulj', 'client_secret': secret,
                  'workspace_id': WORKSPACE, 'user_id': '014542292620111416'}
        result = run_backup(configured_service(config))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except ConnectionFailure as error:
        print(json.dumps({'success': False, 'stage': error.stage, 'code': error.code}))
    except WorkflowFailure as error:
        print(json.dumps({'success': False, 'reason': str(error)}, ensure_ascii=False))
    except Exception:
        print('Backup failed; sensitive details suppressed.')
    return 1


if __name__ == '__main__':
    sys.exit(main())
