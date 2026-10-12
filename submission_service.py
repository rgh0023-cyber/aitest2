"""Authenticated, idempotent submissions. No reviewer or publishing operations."""
import hashlib
import json
import sqlite3
from contextlib import closing
from proposal import canonical
from submission_contract import validate, SubmissionFailure
from document_workflow import archive


def submit(journal,service,payload,principal):
    validated=validate(payload,principal)
    request_hash=hashlib.sha256(canonical(payload).encode()).hexdigest()
    key=principal['subject_id']+':'+payload['request_id']
    with journal.lock,closing(sqlite3.connect(journal.path)) as db,db:
        db.execute('CREATE TABLE IF NOT EXISTS submissions (id TEXT PRIMARY KEY,digest TEXT,proposal TEXT)')
        row=db.execute('SELECT digest,proposal FROM submissions WHERE id=?',(key,)).fetchone()
        if row:
            if row[0]!=request_hash:
                raise SubmissionFailure('请求 ID 已用于不同内容，请为修订生成新的请求 ID。')
            proposal=json.loads(row[1])
        else:
            # Deterministic proposal ID also blocks duplicates after rebuilding this cache.
            receipt_row=db.execute('SELECT receipt,verified FROM operations WHERE id=?',(validated['id']+':proposal',)).fetchone()
            if receipt_row and receipt_row[0] and receipt_row[1]:
                text=''.join(service.read(json.loads(receipt_row[0])))
                marker='\n\n机器快照（待审核）：\n'
                try:
                    proposal=json.loads(text.rsplit(marker,1)[1])
                except (ValueError,IndexError):
                    raise SubmissionFailure('远程提案无法恢复，请管理员核对。') from None
                if proposal.get('submitted_by')!=principal['subject_id'] or proposal.get('request_sha256')!=request_hash:
                    raise SubmissionFailure('历史请求与当前身份或内容冲突。')
            elif receipt_row:
                raise SubmissionFailure('该请求的创建结果不确定，请管理员核对，禁止重复创建。')
            else:
                proposal=validated
            db.execute('INSERT INTO submissions VALUES (?,?,?)',(key,request_hash,canonical(proposal)))
            db.commit()
    receipt=archive(journal,service,proposal)
    return {'proposal_id':proposal['id'],'proposal_sha256':proposal['sha256'],
        'request_id':payload['request_id'],'submitted_by':principal['subject_id'],
        'status':journal.proposals(proposal['id'])[0]['status'],'review_library_url':receipt['url']}
