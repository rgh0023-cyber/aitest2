"""Transport-independent Agent submission contract. No caller-controlled approvals."""
import hashlib
import json
import uuid
from proposal import canonical, create_proposal, fingerprint

PROJECT_ID = 'PRJ-4a5cce5e-e785-57e2-833a-010a6f9394d2'
FIELDS = {'schema_version', 'request_id', 'project_id', 'module', 'title', 'kind', 'reason', 'content',
          'document_id', 'base_revision_id', 'code_bindings'}


class SubmissionFailure(ValueError):
    pass


def uuid_id(value, prefix):
    if not isinstance(value, str) or not value.startswith(prefix):
        raise SubmissionFailure('身份字段格式错误。')
    try:
        parsed = uuid.UUID(value[len(prefix):])
    except ValueError:
        raise SubmissionFailure('身份字段格式错误。') from None
    if str(parsed) != value[len(prefix):]:
        raise SubmissionFailure('身份字段须使用标准小写 UUID。')


def validate(payload, principal):
    if not isinstance(payload, dict) or set(payload) - FIELDS:
        raise SubmissionFailure('请求含未知或未经允许的字段。')
    if payload.get('schema_version') != 1:
        raise SubmissionFailure('不支持的提交协议版本。')
    if payload.get('project_id') != PROJECT_ID or PROJECT_ID not in principal.get('projects', []):
        raise SubmissionFailure('项目未授权。')
    if principal.get('scope') != 'proposal:submit' or not principal.get('subject_id'):
        raise SubmissionFailure('提交身份或权限无效。')
    uuid_id(payload.get('request_id'), '')
    for key, prefix in [('document_id','DOC-'), ('base_revision_id','REV-')]:
        if payload.get(key) is not None:
            uuid_id(payload[key], prefix)
    bindings = payload.get('code_bindings', [])
    if not isinstance(bindings, list) or len(bindings) > 20:
        raise SubmissionFailure('代码关联格式错误或数量过多。')
    for binding in bindings:
        if not isinstance(binding, dict) or set(binding) - {'repository_url','commit_sha','path','symbol','binding_type'}:
            raise SubmissionFailure('代码关联存在未知字段。')
        commit = binding.get('commit_sha', '')
        if not isinstance(commit, str) or len(commit) != 40 or any(c not in '0123456789abcdef' for c in commit):
            raise SubmissionFailure('代码提交必须使用完整小写 SHA。')
        if any(not isinstance(binding.get(k), str) or not binding[k] or len(binding[k]) > 1000 for k in ('repository_url','path')):
            raise SubmissionFailure('代码仓库或路径缺失。')
    try:
        proposal = create_proposal('level tunning', payload.get('module'), payload.get('title'),
            payload.get('kind'), payload.get('reason'), payload.get('content'))
    except ValueError as error:
        raise SubmissionFailure(str(error)) from None
    # Principal is supplied only by server authentication; never accept caller's claimed identity.
    proposal.update(submitted_by=principal['subject_id'], submission_request_id=payload['request_id'],
        project_id=PROJECT_ID, document_id=payload.get('document_id'), base_revision_id=payload.get('base_revision_id'),
        code_bindings=bindings, request_sha256=hashlib.sha256(canonical(payload).encode()).hexdigest())
    proposal['id']='PR-'+uuid.uuid5(uuid.NAMESPACE_URL,'agent-submission:'+principal['subject_id']+':'+payload['request_id']).hex
    proposal['sha256'] = fingerprint(proposal)
    return proposal
