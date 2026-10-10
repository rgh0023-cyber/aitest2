"""Immutable proposal snapshots; no credentials or remote mutations."""
import hashlib
import json
import uuid
from datetime import datetime, timezone, timedelta


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def fingerprint(proposal):
    return hashlib.sha256(canonical({k: v for k, v in proposal.items() if k != 'sha256'}).encode('utf-8')).hexdigest()


def create_proposal(project, module, title, kind, reason, content):
    fields = dict(project=project, module=module, title=title, kind=kind, reason=reason, content=content)
    if any(not isinstance(v, str) or not v.strip() for v in fields.values()):
        raise ValueError('请填写所有提案字段。')
    if kind not in ('正式文档新版本', '修正日志', '会议记录', '问答记录'):
        raise ValueError('不支持的文档类型。')
    if len(content) > 6000:
        raise ValueError('单份正文最多 6000 字，请按主题拆成独立提案。')
    if any(len(fields[k]) > 200 for k in ('project', 'module', 'title')) or len(reason) > 2000:
        raise ValueError('项目、模块和标题最多 200 字；修改原因最多 2000 字。')
    result = dict(fields, id='PR-' + uuid.uuid4().hex,
        created_at=datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds'),
        status='待审核', schema_version=1, action='创建独立文档',
        review_identity='尚未认证', storage='本地提案快照，尚未写入知识库')
    result['sha256'] = fingerprint(result)
    return result


def verify(proposal):
    return isinstance(proposal, dict) and proposal.get('sha256') == fingerprint(proposal)


def export_markdown(proposal):
    if not verify(proposal):
        raise ValueError('提案内容已变化，请重新生成快照。')
    return '\n'.join([
        '# ' + proposal['title'], '',
        '* 状态：待审核，未写入知识库',
        '* 提案编号：' + proposal['id'],
        '* 创建时间：' + proposal['created_at'],
        '* 项目：' + proposal['project'],
        '* 模块或主题：' + proposal['module'],
        '* 类型：' + proposal['kind'],
        '* 变更范围：创建独立文档，保留已有版本',
        '* SHA-256：' + proposal['sha256'], '',
        '## 变更原因', '', proposal['reason'], '',
        '## 提案正文', '', proposal['content'], '',
        '## 审核与落库', '',
        '尚未完成审核身份认证；本快照不代表正式文档或写入授权。',
    ])
