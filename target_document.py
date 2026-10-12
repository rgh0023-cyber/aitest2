"""Resolve a formal project target without accepting arbitrary hosts or projects."""
import hashlib
import re
import uuid
from urllib.parse import quote, urlencode
from dingtalk_client import request_json
from document_workflow import WORKSPACE, WorkflowFailure
from proposal import canonical

PROJECT_ROOT = '2Amq4vjg89gGQ1rbFxygRa5lV3kdP0wQ'


def resolve(service, url):
    match = re.fullmatch(r'https://alidocs\.dingtalk\.com/i/nodes/([A-Za-z0-9]+)', url.strip())
    if not match:
        raise WorkflowFailure('请输入 level tunning 正式文档的钉钉节点链接。')
    node_id = match[1]
    pending, seen, count = [PROJECT_ROOT], set(), 0
    while pending:
        folder = pending.pop()
        if folder in seen:
            raise WorkflowFailure('项目目录存在重复节点，核验停止。')
        seen.add(folder)
        for node in service.list_nodes(folder):
            count += 1
            if count > 5000 or len(seen) > 100:
                raise WorkflowFailure('项目目录超过扫描上限，核验停止。')
            if node.get('workspaceId') != WORKSPACE:
                raise WorkflowFailure('项目节点知识库归属异常。')
            if node.get('type') == 'FOLDER':
                pending.append(node['nodeId'])
            elif node.get('nodeId') == node_id:
                if node.get('extension') != 'adoc':
                    raise WorkflowFailure('当前只支持在线文字文档作为目标。')
                response = request_json(service.blocks_url({'docKey': node_id}), '目标文档基线回读', headers=service.headers)
                result = response.get('result', {})
                if response.get('success') is not True or not isinstance(result.get('data'), list) or result.get('hasMore') or result.get('nextToken'):
                    raise WorkflowFailure('目标文档基线未完整读取，提交已停止。')
                text = canonical(result['data'])
                return {'url': url.strip(), 'node_id': node_id,
                        'document_id': 'DOC-' + str(uuid.uuid5(uuid.NAMESPACE_URL, 'dingtalk:node:' + node_id)),
                        'content_sha256': hashlib.sha256(text.encode()).hexdigest(), 'content': text}
    raise WorkflowFailure('目标不属于 level tunning 正式项目，提交已拒绝。')
