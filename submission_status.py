"""Private, read-only status lookup for a verified submitter's own proposal."""
import re
from project_access import require_identity
from document_workflow import WorkflowFailure


def lookup(journal, service, identity, proposal_id):
    require_identity(identity)
    if not isinstance(proposal_id,str) or not re.fullmatch(r'PR-[0-9a-f]{32}',proposal_id):
        raise WorkflowFailure('请输入有效提案编号。')
    entries=journal.proposals(proposal_id)
    if not entries:
        raise WorkflowFailure('未找到属于你的提案，请核对编号与钉钉登录身份。')
    entry=entries[0]
    proposal=journal.load_proposal(service,entry,readonly=True)
    if proposal.get('submitted_by')!='DING-'+identity['union_id']:
        raise WorkflowFailure('未找到属于你的提案，请核对编号与钉钉登录身份。')
    result={'proposal_id':proposal_id,'project':proposal['project'],'title':proposal['title'],
            'status':entry['status'],'review_library_url':entry['receipt']['url'],
            'formal_document_url':None,'record_url':None,'decision_url':None,
            'library_state':'临时／待审核','is_formal':False}
    if entry['status']=='已拒绝':
        result['decision_url']=journal.verified_step(service,proposal_id,'rejection')['url']
        result['library_state']='已拒绝／保留过程记录'
    elif entry['status']=='已发布':
        result['decision_url']=journal.verified_step(service,proposal_id,'approval')['url']
        published=journal.verified_step(service,proposal_id,'published')
        journal.verified_step(service,proposal_id,'change')
        result['record_url']=published['url']
        if proposal['kind']=='正式文档新版本':
            result.update(is_formal=True,library_state='正式文档／已发布版本',
                          formal_document_url=published['url'],effective_index_status='待更新')
        else:
            result['library_state']='已审核／过程记录'
    elif entry['status']!='待审核':
        result['library_state']='执行未确认／不能认定正式'
    return result
