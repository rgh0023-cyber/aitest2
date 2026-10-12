"""Signed submission grants and revocations live in the review library, never raw tokens."""
import hashlib
import hmac
import json
import secrets
import time
import uuid
from document_workflow import REVIEW_FOLDER, WorkflowFailure
from proposal import canonical
from submission_contract import PROJECT_ID

PREFIX = 'Agent接入｜'


def grant(service, label, days=7):
    if not isinstance(label, str) or not label.strip() or len(label) > 80 or not 1 <= days <= 30:
        raise WorkflowFailure('参与者标识必须为 1–80 字，有效期为 1–30 天。')
    credential_id = 'CRED-' + str(uuid.uuid4())
    token = 'adt_' + secrets.token_urlsafe(32)
    payload = {'schema': 1, 'credential_id': credential_id, 'subject_id': 'AGENT-' + str(uuid.uuid4()),
        'label': label.strip(), 'scope': 'proposal:submit', 'projects': [PROJECT_ID],
        'token_sha256': hashlib.sha256(token.encode()).hexdigest(), 'issued_at': time.time(),
        'expires_at': time.time() + days*86400, 'action': 'grant'}
    receipt = service.create(PREFIX + '授权｜' + credential_id, REVIEW_FOLDER)
    service.ensure_content(receipt, canonical({'payload': payload, 'signature': service.seal(payload)}))
    return token, payload, receipt


def revoke(service, credential_id):
    payload = {'schema':1,'action':'revoke','credential_id':credential_id,'revoked_at':time.time()}
    receipt = service.create(PREFIX + '撤销｜' + credential_id + '｜' + uuid.uuid4().hex[:8], REVIEW_FOLDER)
    service.ensure_content(receipt, canonical({'payload':payload,'signature':service.seal(payload)}))
    return receipt


def credentials(service):
    grants, revoked = {}, set()
    for node in service.list_nodes(REVIEW_FOLDER):
        if not node.get('name','').startswith(PREFIX):
            continue
        try:
            value = json.loads(''.join(service.read({'docKey':node['nodeId']})))
            payload, signature = value['payload'], value['signature']
            if not isinstance(signature,str) or not hmac.compare_digest(signature,service.seal(payload)):
                raise ValueError('signature')
            if payload.get('schema') != 1:
                raise ValueError('schema')
            key = payload['credential_id']
            if payload.get('action') == 'revoke':
                revoked.add(key)
            elif payload.get('action') == 'grant':
                if key in grants and grants[key] != payload:
                    raise ValueError('conflicting grant')
                grants[key] = payload
            else:
                raise ValueError('action')
        except (ValueError, KeyError, TypeError):
            # Missing/modified revocations must never silently restore a credential.
            raise WorkflowFailure('接入凭据记录不完整或被改动，提交暂时关闭，请管理员核对。') from None
    return [dict(value, revoked=key in revoked) for key,value in grants.items()]


def authenticate(service, token):
    if not isinstance(token,str) or not token.startswith('adt_') or not 40 <= len(token) <= 100:
        raise WorkflowFailure('提交凭据无效。')
    hashed = hashlib.sha256(token.encode()).hexdigest()
    for item in credentials(service):
        if hmac.compare_digest(item['token_sha256'],hashed):
            if item['revoked'] or item['expires_at'] <= time.time() or item['scope'] != 'proposal:submit':
                break
            return {key:item[key] for key in ('credential_id','subject_id','label','scope','projects','expires_at')}
    raise WorkflowFailure('提交凭据无效、已撤销或已过期。')
