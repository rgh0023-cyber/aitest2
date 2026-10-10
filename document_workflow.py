"""Single-owner pilot. Durable remote documents; local execution receipts are not a queue."""
import hashlib
import json
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime, timezone, timedelta
from urllib.parse import quote, urlencode
from dingtalk_client import request_json, ConnectionFailure
from proposal import verify, export_markdown, canonical

WORKSPACE = 'X1v4RSYLD9bwDk8P'
FOLDERS = {
    'proposal': 'ydxXB52LJq7L2azjHZGdOYe3WqjMp697',
    'approval': 'ydxXB52LJq7L2azjHZGdOYe3WqjMp697',
    '正式文档新版本': 'm9bN7RYPWdlmdvK5ujE96MvzWZd1wyK0',
    '修正日志': 'ydxXB52LJq7L2azjHZGdOYe3WqjMp697',
    '会议记录': 'Gl6Pm2Db8D30gXDBILxDpr3mJxLq0Ee4',
    '问答记录': 'Gl6Pm2Db8D30gXDBILxDpr3mJxLq0Ee4',
    'change': 'Gl6Pm2Db8D30gXDBILxDRNZnJxLq0Ee4',
}


class WorkflowFailure(Exception):
    pass


def now():
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')


class DocumentService:
    def __init__(self, config, identity):
        if config['workspace_id'] != WORKSPACE:
            raise WorkflowFailure('当前试运行只允许写入已核验的 AI 文档库。')
        if not identity or identity.get('expires_at', 0) <= time.time():
            raise WorkflowFailure('审核身份已失效，请重新登录。')
        auth = request_json('https://api.dingtalk.com/v1.0/oauth2/accessToken', '写入身份认证',
            {'appKey': config['client_id'], 'appSecret': config['client_secret']})
        token = auth.get('accessToken')
        if not isinstance(token, str) or not token:
            raise WorkflowFailure('无法获得应用凭据。')
        user = request_json('https://oapi.dingtalk.com/topapi/v2/user/get?' +
            urlencode({'access_token': token}), '写入操作人校验',
            {'userid': config.get('user_id', '014542292620111416')})
        operator = user.get('result', {}).get('unionid')
        if not operator or operator != identity.get('union_id'):
            raise WorkflowFailure('登录用户与服务操作人不一致。')
        self.headers = {'x-acs-dingtalk-access-token': token}
        self.operator = operator

    def validate_folder(self, folder):
        result = request_json('https://api.dingtalk.com/v2.0/wiki/nodes/' + quote(folder, safe='') +
            '?' + urlencode({'operatorId': self.operator}), '目标目录校验', headers=self.headers)
        node = result.get('node', {})
        if node.get('workspaceId') != WORKSPACE or node.get('type') != 'FOLDER' or node.get('nodeId') != folder:
            raise WorkflowFailure('目标目录类型或知识库归属不一致。')

    def create(self, title, folder):
        self.validate_folder(folder)
        result = request_json('https://api.dingtalk.com/v1.0/doc/workspaces/' + WORKSPACE + '/docs',
            '创建独立记录', {'name': title, 'docType': 'DOC', 'operatorId': self.operator,
                          'parentNodeId': folder}, self.headers)
        if not all(isinstance(result.get(k), str) and result[k] for k in ('docKey', 'nodeId', 'url')):
            raise WorkflowFailure('创建回执不完整；请人工核对，禁止重复创建。')
        if not result['url'].startswith('https://alidocs.dingtalk.com/i/nodes/'):
            raise WorkflowFailure('文档回执地址异常。')
        return {k: result[k] for k in ('docKey', 'nodeId', 'url')}

    def blocks_url(self, receipt):
        return 'https://api.dingtalk.com/v1.0/doc/suites/documents/' + quote(receipt['docKey'], safe='') + '/blocks?' + urlencode({'operatorId': self.operator})

    def read(self, receipt):
        result = request_json(self.blocks_url(receipt), '记录回读', headers=self.headers)
        if result.get('success') is not True or not isinstance(result.get('result', {}).get('data'), list):
            raise WorkflowFailure('回读结构异常，写入未验收。')
        # We create at most 20 blocks; any pagination signal is treated as incomplete.
        if result.get('result', {}).get('nextToken') or result.get('result', {}).get('hasMore'):
            raise WorkflowFailure('回读未完成，写入未验收。')
        values = []
        for block in result['result']['data']:
            if block.get('blockType') != 'paragraph':
                raise WorkflowFailure('记录含非预期内容，停止写入。')
            value = block.get('paragraph', {}).get('text', '')
            if value:
                values.append(value)
        return values

    def ensure_content(self, receipt, text):
        expected = [text[i:i + 1800] for i in range(0, len(text), 1800)]
        if len(expected) > 20:
            raise WorkflowFailure('记录过长，请拆分。')
        actual = self.read(receipt)
        if actual != expected[:len(actual)]:
            raise WorkflowFailure('远程内容与预期不一致，停止写入，请人工核对。')
        for chunk in expected[len(actual):]:
            result = request_json(self.blocks_url(receipt), '追加记录正文',
                {'element': {'blockType': 'paragraph', 'paragraph': {'text': chunk}}}, self.headers)
            if result.get('success') is not True:
                raise WorkflowFailure('正文写入未返回成功，请回读核对。')
        if self.read(receipt) != expected:
            raise WorkflowFailure('正文回读不一致，记录尚未验收。')


class Journal:
    def __init__(self, path='workflow.sqlite3'):
        self.path = path
        self.lock = threading.RLock()
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY, digest TEXT, receipt TEXT, verified INTEGER DEFAULT 0)')

    def proposals(self):
        """Locate remote documents using receipts; never treat local data as content."""
        with self.lock, closing(sqlite3.connect(self.path)) as db:
            rows = db.execute("SELECT id,receipt,verified FROM operations WHERE id LIKE '%:proposal' ORDER BY rowid DESC LIMIT 100").fetchall()
            result = []
            for key, raw, verified in rows:
                if not raw or not verified:
                    continue
                proposal_id = key.removesuffix(':proposal')
                phases = dict(db.execute('SELECT id,verified FROM operations WHERE id IN (?,?,?)',
                    tuple(proposal_id + ':' + phase for phase in ('approval', 'published', 'change'))).fetchall())
                status = '待审核'
                if proposal_id + ':approval' in phases:
                    status = '审批执行中（需核对）'
                if phases.get(proposal_id + ':change'):
                    status = '已发布'
                result.append({'id': proposal_id, 'receipt': json.loads(raw), 'status': status})
            return result

    def load_proposal(self, service, entry):
        if entry['status'] != '待审核':
            raise WorkflowFailure('该提案已进入审批或发布，不可作为新的待审提案批准。')
        text = ''.join(service.read(entry['receipt']))
        marker = '\n\n机器快照（待审核）：\n'
        if marker not in text:
            raise WorkflowFailure('远程提案不包含可校验快照，请人工核对。')
        try:
            proposal = json.loads(text.rsplit(marker, 1)[1])
        except (ValueError, TypeError):
            raise WorkflowFailure('远程提案快照格式异常。') from None
        check_proposal(proposal)
        if proposal['id'] != entry['id']:
            raise WorkflowFailure('远程提案编号与回执不一致。')
        expected = export_markdown(proposal) + marker + canonical(proposal)
        if text != expected:
            raise WorkflowFailure('远程正文与快照不一致，禁止加载审批。')
        with self.lock, closing(sqlite3.connect(self.path)) as db:
            row = db.execute('SELECT digest FROM operations WHERE id=?', (entry['id'] + ':proposal',)).fetchone()
        if not row or row[0] != hashlib.sha256(text.encode()).hexdigest():
            raise WorkflowFailure('远程提案内容与初次落库指纹不同，禁止加载审批。')
        return proposal

    def document(self, service, key, title, folder, text):
        digest = hashlib.sha256(text.encode()).hexdigest()
        with self.lock, closing(sqlite3.connect(self.path)) as db, db:
            row = db.execute('SELECT digest,receipt,verified FROM operations WHERE id=?', (key,)).fetchone()
            if row:
                if row[0] != digest:
                    raise WorkflowFailure('同一操作的内容发生变化，必须生成新提案。')
                if not row[1]:
                    raise WorkflowFailure('该创建操作结果不确定。请核对知识库后由管理员恢复，不可自动重试。')
                receipt = json.loads(row[1])
            else:
                db.execute('INSERT INTO operations (id,digest) VALUES (?,?)', (key, digest))
                db.commit()  # Claim before remote mutation, including across sessions.
                receipt = service.create(title, folder)
                db.execute('UPDATE operations SET receipt=? WHERE id=?', (canonical(receipt), key))
                db.commit()
            # Always read, even previously verified documents; never trust stale receipts.
            service.ensure_content(receipt, text)
            db.execute('UPDATE operations SET verified=1 WHERE id=?', (key,))
            return receipt


def check_proposal(proposal):
    if not verify(proposal) or proposal.get('project') != 'level tunning' or proposal.get('kind') not in FOLDERS:
        raise WorkflowFailure('提案指纹、项目或类型无效。')


def title(proposal, kind):
    stamp = proposal['created_at'][:19].replace('T', ' ').replace(':', '')
    return stamp + '｜' + kind + '｜' + proposal['id'] + '｜' + proposal['title'][:60]


def archive(journal, service, proposal):
    check_proposal(proposal)
    return journal.document(service, proposal['id'] + ':proposal', title(proposal, '待审核提案'),
        FOLDERS['proposal'], export_markdown(proposal) + '\n\n机器快照（待审核）：\n' + canonical(proposal))


def publish(journal, service, proposal, identity, approval, proposal_receipt):
    check_proposal(proposal)
    if identity.get('expires_at', 0) <= time.time() or identity.get('union_id') != service.operator:
        raise WorkflowFailure('审核身份已失效或不匹配。')
    if approval.get('proposal_hash') != proposal['sha256'] or approval.get('reviewer') != identity['union_id']:
        raise WorkflowFailure('审核未绑定当前提案与登录身份。')
    # Reverify archived snapshot before committing the approval.
    archived = archive(journal, service, proposal)
    if archived != proposal_receipt:
        raise WorkflowFailure('提案落库回执不匹配。')
    review_text = '人工批准记录\n' + canonical(approval) + '\n提案：' + archived['url']
    review = journal.document(service, proposal['id'] + ':approval', title(proposal, '人工审核批准'), FOLDERS['approval'], review_text)
    text = '\n'.join([proposal['title'], '状态：已批准发布', '模块或主题：' + proposal['module'],
        '提案：' + archived['url'], '审核记录：' + review['url'], '内容指纹：' + proposal['sha256'], '', proposal['content']])
    document = journal.document(service, proposal['id'] + ':published', title(proposal, proposal['kind']), FOLDERS[proposal['kind']], text)
    change_text = '版本发布与回读验证记录\n' + canonical({'proposal_id': proposal['id'],
        'proposal_hash': proposal['sha256'], 'reason': proposal['reason'],
        'reviewer': approval['reviewer'], 'approved_at': approval['approved_at'],
        'proposal_url': archived['url'], 'approval_url': review['url'], 'published_url': document['url'],
        'verification': '正文逐块精确回读通过', 'index_status': '尚未更新项目索引'})
    change = journal.document(service, proposal['id'] + ':change', title(proposal, '版本变更'), FOLDERS['change'], change_text)
    return {'proposal': archived, 'approval': review, 'published': document, 'change': change}
