"""Rolling full backup plus signed, reversible historical diffs. No retention deletes."""
import base64
import difflib
import hashlib
import json
from document_workflow import BACKUP_FOLDER, FOLDERS, WorkflowFailure, now
from dingtalk_client import request_json
from proposal import canonical

LATEST_NAME = 'level tunning｜最新完整备份'


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def encode(service, value):
    return base64.b64encode(canonical({'payload': value, 'signature': service.seal(value)}).encode()).decode()


def decode(service, text):
    try:
        record = json.loads(base64.b64decode(text, validate=True))
        value = record['payload']
        import hmac
        if not hmac.compare_digest(record['signature'], service.seal(value)):
            raise ValueError('signature')
        return value
    except (ValueError, TypeError, KeyError):
        raise WorkflowFailure('备份或 diff 的格式、签名不正确，停止覆盖。') from None


def make_delta(before, after):
    left = json.dumps(before, ensure_ascii=False, sort_keys=True, indent=2).splitlines(keepends=True)
    right = json.dumps(after, ensure_ascii=False, sort_keys=True, indent=2).splitlines(keepends=True)
    return {'before_sha256': digest(before), 'after_sha256': digest(after),
            'ndiff': list(difflib.ndiff(left, right))}


def restore_delta(delta, direction):
    value = json.loads(''.join(difflib.restore(delta['ndiff'], direction)))
    expected = delta['before_sha256'] if direction == 1 else delta['after_sha256']
    if digest(value) != expected:
        raise WorkflowFailure('diff 还原校验失败。')
    return value


def node_receipt(node):
    return {'docKey': node['nodeId'], 'nodeId': node['nodeId'], 'url': node['url'].split('?')[0]}


def collect_formal(service):
    """Read every page and every document block, including rich content, before any write."""
    pending = [FOLDERS['正式文档新版本']]
    seen, documents = set(), {}
    while pending:
        folder = pending.pop()
        if folder in seen:
            raise WorkflowFailure('正式库目录循环，备份停止。')
        seen.add(folder)
        if len(seen) > 100:
            raise WorkflowFailure('目录过多，备份停止。')
        for node in service.list_nodes(folder):
            if node.get('type') == 'FOLDER':
                pending.append(node['nodeId'])
                continue
            if node.get('extension') != 'adoc':
                raise WorkflowFailure('正式目录含非文字文档，本轮不能完整备份，已停止。')
            receipt = node_receipt(node)
            response = request_json(service.blocks_url(receipt), '正式文档完整备份读取', headers=service.headers)
            result = response.get('result', {})
            if response.get('success') is not True or not isinstance(result.get('data'), list) or result.get('hasMore') or result.get('nextToken'):
                raise WorkflowFailure('源文档正文未完整读取，备份停止。')
            documents[node['nodeId']] = {'name': node['name'], 'url': receipt['url'],
                'folder': folder, 'blocks': result['data']}
    return {'schema': 1, 'project': 'level tunning', 'formal_root': FOLDERS['正式文档新版本'], 'documents': documents}


def run_backup(service, snapshot=None):
    snapshot = collect_formal(service) if snapshot is None else snapshot
    if snapshot.get('project') != 'level tunning':
        raise WorkflowFailure('备份项目不一致。')
    nodes = service.list_nodes(BACKUP_FOLDER)
    latest = [node for node in nodes if node.get('name') == LATEST_NAME]
    if len(latest) > 1:
        raise WorkflowFailure('最新备份存在重复文档，停止覆盖。')
    captured = now()
    new_payload = {'schema': 1, 'project': 'level tunning', 'captured_at': captured, 'snapshot': snapshot, 'sha256': digest(snapshot)}
    if not latest:
        receipt = service.create(LATEST_NAME, BACKUP_FOLDER)
        service.ensure_content(receipt, encode(service, new_payload))
        return {'status': '已建立初始基线', 'latest': receipt, 'sha256': new_payload['sha256'], 'diff': None}
    receipt = node_receipt(latest[0])
    previous = decode(service, ''.join(service.read(receipt)))
    if previous.get('project') != 'level tunning' or previous.get('sha256') != digest(previous['snapshot']):
        raise WorkflowFailure('旧备份项目或校验值不一致。')
    if previous['sha256'] == new_payload['sha256']:
        return {'status': '源内容无变化', 'latest': receipt, 'sha256': previous['sha256'], 'diff': None}
    delta = make_delta(previous['snapshot'], snapshot)
    assert restore_delta(delta, 1) == previous['snapshot'] and restore_delta(delta, 2) == snapshot
    transition = delta['before_sha256'] + '-' + delta['after_sha256']
    diff_name = 'level tunning｜历史diff｜' + transition
    existing = [node for node in nodes if node.get('name') == diff_name]
    if len(existing) > 1:
        raise WorkflowFailure('diff 存在重复记录，停止覆盖。')
    record = {'schema': 1, 'project': 'level tunning', 'captured_at': captured, 'delta': delta,
        'latest_url': receipt['url'], 'operation': '先验证diff，再覆盖完整副本'}
    if existing:
        diff_receipt = node_receipt(existing[0])
        saved = decode(service, ''.join(service.read(diff_receipt)))
        if saved.get('delta') != delta:
            raise WorkflowFailure('已有 diff 与本轮变化不一致，停止覆盖。')
    else:
        diff_receipt = service.create(diff_name, BACKUP_FOLDER)
        service.ensure_content(diff_receipt, encode(service, record))
    # Re-read before replacing: a changed backup must never be overwritten blindly.
    if decode(service, ''.join(service.read(receipt))) != previous:
        raise WorkflowFailure('备份在执行期间发生变化，已保存diff但未覆盖。')
    content = encode(service, new_payload)
    if len(content) > 500000:
        raise WorkflowFailure('备份正文超过试运行上限，未覆盖。')
    url = service.blocks_url(receipt).replace('/blocks?', '/overwriteContent?')
    result = request_json(url, '覆盖最新完整备份', {'content': content, 'dataType': 'markdown'}, service.headers)
    if result.get('success') is not True:
        raise WorkflowFailure('备份覆盖未确认成功，请核对后重试。')
    actual = decode(service, ''.join(service.read(receipt)))
    if actual != new_payload:
        raise WorkflowFailure('覆盖回读不一致，保留diff待核对。')
    return {'status': 'diff已验证，完整备份已覆盖并回读', 'latest': receipt,
            'sha256': new_payload['sha256'], 'diff': diff_receipt}
