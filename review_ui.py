import time
import json
import streamlit as st
from dingtalk_client import ConnectionFailure, test_connection
from proposal import create_proposal, export_markdown, verify
from document_workflow import DocumentService, WorkflowFailure, archive, publish, reject, now
from backup_service import run_backup
from dashboard_model import STATUSES, filter_proposals


def render_review(config, identity, execution_journal):
    st.header('提案审核')
    controls = st.columns([1, 3])
    status = controls[0].selectbox('状态', ['待审核', '全部', '已发布', '已拒绝', '审批执行中（需核对）'])
    if controls[1].button('刷新列表', key='review_refresh'):
        st.session_state.pop('cloud_recovery_v4', None)
    if not st.session_state.get('cloud_recovery_v4'):
        try:
            with st.spinner('读取提案列表…'):
                st.session_state['cloud_recovery_v4'] = execution_journal().sync_and_restore(DocumentService(config, identity))
        except (WorkflowFailure, ConnectionFailure):
            st.error('提案列表未完整核验，暂时不能审核，请刷新重试。')
            return
    entries = filter_proposals(execution_journal().proposals(), status)
    notice = st.session_state.pop('review_notice', None)
    if notice:
        st.success(notice['message'])
        if notice.get('url'):
            st.link_button('查看审核结果', notice['url'])
    report = st.session_state['cloud_recovery_v4']
    if report.get('warnings'):
        with st.expander('需要核对的记录'):
            for warning in report['warnings']:
                st.text(warning)
    if not entries:
        st.info('暂无符合此状态的提案。')
        return
    widths=[1.1,1.5,2.5,1,0.9,1,1]
    headings=st.columns(widths)
    for col,label in zip(headings,['项目','修改时间','标题','状态','详情','通过','拒绝']):
        col.write(label)
    st.divider()
    for entry in entries:
        cols=st.columns(widths)
        cols[0].text(entry.get('project') or 'level tunning')
        # Proposals are immutable snapshots; their last modification is creation.
        cols[1].text((entry.get('created_at') or '待核验').replace('T',' ')[:19])
        cols[2].text(entry.get('title') or entry['id'])
        cols[3].text(entry['status'])
        cols[4].link_button('详情',entry['receipt']['url'])
        pending=entry['status']=='待审核'
        approve=cols[5].button('审核通过',key='approve_'+entry['id'],disabled=not pending)
        deny=cols[6].button('审核拒绝',key='reject_'+entry['id'],disabled=not pending)
        if approve or deny:
            try:
                service=DocumentService(config,identity)
                snapshot=execution_journal().load_proposal(service,entry)
                action='approved' if approve else 'rejected'
                key=entry['id']+':'+action
                decisions=st.session_state.setdefault('row_review_decisions',{})
                decision=decisions.get(key)
                if not decision:
                    decision={'proposal_id':snapshot['id'],'proposal_hash':snapshot['sha256'],
                        'reviewer':identity['union_id'],'reviewer_name':identity['name'],
                        'approved_at':now(),'notes':'',
                        'decision':'批准创建独立文档' if approve else '拒绝发布'}
                    decisions[key]=decision
                with st.spinner('保存审核结果…'):
                    if approve:
                        receipts=publish(execution_journal(),service,snapshot,identity,decision,entry['receipt'])
                        message='审核通过，发布及变更记录已核验；项目索引仍待更新。'
                        url=receipts['published']['url']
                        try:
                            st.session_state['backup_result']=run_backup(service)
                        except Exception:
                            message+='备份未完成，请到运行与备份 Tab 重试。'
                    else:
                        receipt=reject(execution_journal(),service,snapshot,identity,decision,entry['receipt'])
                        message='审核已拒绝，决定已记录，原提案保留。'
                        url=receipt['url']
                st.session_state['review_notice']={'message':message,'url':url}
                st.rerun()
            except (WorkflowFailure,ConnectionFailure) as error:
                st.error(str(error) if isinstance(error,WorkflowFailure) else '审核中断：'+error.stage+'；'+str(error.code))
                st.warning('先核对已有记录；不要换提案编号重复执行。')
            except Exception:
                st.error('审核结果未确认，请先核对钉钉记录。')
        st.divider()


def render_create(config, identity, execution_journal):
    st.divider()
    st.header('创建提案')
    st.info('提案与批准记录进入「AI文档协作与审核库」；仅正式文档新版本进入正式库。会议、问答及修正记录保存在审核库。')
    st.caption('将 Agent 生成的内容粘贴或上传到这里。每次生成独立快照，修改后需重新生成。当前快照仅在本次会话中保存，请及时下载；刷新、退出或服务重启可能丢失。')
    uploaded = st.file_uploader('导入 Agent 生成的 Markdown 正文（最多 1 MB）', type=['md', 'txt'])
    imported = ''
    if uploaded:
        try:
            if uploaded.size > 1024 * 1024:
                st.error('文件超过 1 MB，请拆分后导入。')
            else:
                imported = uploaded.getvalue().decode('utf-8-sig')
        except UnicodeDecodeError:
            st.error('文件必须使用 UTF-8 编码。')
    with st.form('proposal_form'):
        project = st.text_input('项目', value='level tunning', max_chars=200)
        module = st.text_input('功能模块或沟通主题', max_chars=200)
        kind = st.selectbox('记录类型', ['正式文档新版本', '修正日志', '会议记录', '问答记录'])
        title = st.text_input('文档标题', max_chars=200)
        reason = st.text_area('变更原因、依据及关联的旧版本', max_chars=2000)
        content = st.text_area('Agent 提案正文', value=imported, height=300, max_chars=6000)
        submitted = st.form_submit_button('生成待审核快照')
    if submitted:
        try:
            snapshot = create_proposal(project, module, title, kind, reason, content)
            st.session_state['draft_snapshot'] = snapshot
            st.success('已生成待审核快照。')
        except ValueError as error:
            st.error(str(error))

    if submitted and st.session_state.get('draft_snapshot'):
        st.info('草稿已生成；保存为待审核提案后会进入审核列表。')
    snapshot=st.session_state.get('draft_snapshot')
    if snapshot and st.button('保存草稿到审核库'):
        try:
            archive(execution_journal(), DocumentService(config, identity), snapshot)
            st.success('已保存，请在提案审核 Tab 刷新列表查看。')
        except (WorkflowFailure, ConnectionFailure):
            st.error('保存未确认成功，请核对后重试。')
    if snapshot:
        st.text(snapshot['title'])
        st.text(snapshot['content'])


def render_operations(config, identity):
    st.info('点击后使用企业应用凭据认证，以配置的操作人读取目标知识库；不会创建、修改或删除文档。')
    if st.button('测试钉钉连接', type='primary'):
        if time.time() - st.session_state.get('last_test', 0) < 10:
            st.warning('请至少间隔 10 秒再测试。')
        else:
            st.session_state['last_test'] = time.time()
            try:
                with st.spinner('正在验证应用认证、操作人身份和知识库读取…'):
                    result = test_connection(config)
                st.success('三项只读连接测试通过。')
                st.table([{'项目': key, '结果': value} for key, value in result.items()])
            except ConnectionFailure as error:
                st.error('失败环节：' + error.stage + '；错误码：' + str(error.code))
                if error.scopes:
                    st.warning('接口要求权限：' + '、'.join(error.scopes))
                st.caption('密钥、访问凭证和原始错误响应已隐藏。')
            except Exception:
                st.error('配置或运行异常。请检查配置；页面不会显示敏感诊断详情。')

    st.divider()
    st.header('level tunning 备份')
    st.caption('最新完整副本覆盖更新；历史 diff 长期保留。先验证 diff 再覆盖副本。每日云端定时任务尚未启用。')
    backup_identity = st.session_state.get('reviewer_identity')
    backup_allowed = bool(backup_identity and backup_identity.get('expires_at', 0) > time.time())
    if st.button('立即执行备份', disabled=not backup_allowed):
        try:
            with st.spinner('读取正式库、验证 diff 并更新备份…'):
                st.session_state['backup_result'] = run_backup(DocumentService(config, backup_identity))
        except (WorkflowFailure, ConnectionFailure) as error:
            st.error(str(error) if isinstance(error, WorkflowFailure) else '备份失败：' + error.stage + '；' + str(error.code))
        except Exception:
            st.error('备份异常，请先核对已有备份。')
    backup_result = st.session_state.get('backup_result')
    if backup_result:
        st.success(backup_result['status'])
        st.link_button('查看最新完整备份', backup_result['latest']['url'])
        if backup_result['diff']:
            st.link_button('查看本次历史 diff', backup_result['diff']['url'])
        st.download_button('下载备份回执', json.dumps(backup_result, ensure_ascii=False, indent=2),
            file_name='level-tunning-backup-receipt.json', mime='application/json')


