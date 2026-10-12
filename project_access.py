"""Verified DingTalk identities, with signed project grants in the review library."""
import hmac
import json
import time
import uuid
from document_workflow import REVIEW_FOLDER, WorkflowFailure
from proposal import canonical
from submission_contract import PROJECT_ID

PREFIX = '项目接入｜'


def records(service):
    result = []
    for node in service.list_nodes(REVIEW_FOLDER):
        if not node.get('name', '').startswith(PREFIX):
            continue
        try:
            envelope = json.loads(''.join(service.read({'docKey': node['nodeId']})))
            item = envelope['payload']
            if not hmac.compare_digest(envelope['signature'], service.seal(item)):
                raise ValueError()
            if item['project_id'] != PROJECT_ID or item['action'] not in ('request', 'grant', 'revoke'):
                raise ValueError()
            if not isinstance(item['union_id'], str) or not item['union_id']:
                raise ValueError()
            result.append(item)
        except (KeyError, TypeError, ValueError):
            raise WorkflowFailure('项目接入记录无法核验，请管理员检查。') from None
    return result


def write(service, identity, action, days=30):
    if action not in ('request', 'grant', 'revoke') or not 1 <= days <= 365:
        raise WorkflowFailure('接入操作无效。')
    item = {'id': str(uuid.uuid4()), 'union_id': identity['union_id'],
            'name': identity['name'], 'project_id': PROJECT_ID, 'action': action,
            'created_at': time.time(), 'expires_at': time.time() + days * 86400}
    receipt = service.create(PREFIX + action + '｜' + item['id'], REVIEW_FOLDER)
    service.ensure_content(receipt, canonical({'payload': item, 'signature': service.seal(item)}))
    return item


def request_access(service, identity):
    require_identity(identity)
    if not any(r['union_id'] == identity['union_id'] for r in records(service)):
        write(service, identity, 'request')


def require_identity(identity):
    if not identity or identity.get('expires_at', 0) <= time.time() or not identity.get('union_id'):
        raise WorkflowFailure('请先由本人完成钉钉登录。')


def authorize(service, identity):
    require_identity(identity)
    if identity['union_id'] != service.operator:
        actions = [r for r in records(service) if r['union_id'] == identity['union_id'] and r['action'] != 'request']
        actions.sort(key=lambda r: (r['created_at'], r['id']))
        if not actions or actions[-1]['action'] != 'grant' or actions[-1]['expires_at'] <= time.time():
            raise WorkflowFailure('尚未获得 level tunning 提交权限，请等待管理员批准接入。')
    return {'subject_id': 'DING-' + identity['union_id'], 'label': identity['name'],
            'scope': 'proposal:submit', 'projects': [PROJECT_ID], 'expires_at': identity['expires_at']}
