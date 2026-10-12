import time
import json
import streamlit as st
from dingtalk_client import ConnectionFailure, test_connection
from proposal import create_proposal, export_markdown, verify
from document_workflow import DocumentService, WorkflowFailure, archive, publish, now
from backup_service import run_backup
from dashboard_model import STATUSES, filter_proposals


def render_review(config, identity, execution_journal):
    st.header('提案审核')
    st.caption('列表显示最近 100 条已定位提案。查看详情时回读正文；恢复与登录均不会自动批准。')
    if st.button('刷新提案列表', key='review_refresh'):
        st.session_state.pop('cloud_recovery_v4', None)
        st.session_state.pop('review_loaded_id', None)
    if not st.session_state.get('cloud_recovery_v4'):
        try:
            with st.spinner('从钉钉校验提案和执行记录…'):
                report = execution_journal().sync_and_restore(DocumentService(config, identity))
            st.session_state['cloud_recovery_v4'] = report
        except (WorkflowFailure, ConnectionFailure) as error:
            st.error(str(error) if isinstance(error, WorkflowFailure) else '恢复失败：'+error.stage+'；'+str(error.code))
            st.info('恢复未完成，提案审核与创建暂时关闭；可继续查看项目权限管理。')
            return
        except Exception:
            st.error('恢复异常，审核与创建暂时关闭。')
            return
    report = st.session_state['cloud_recovery_v4']
    if report['warnings']:
        with st.expander('需要核对的恢复记录'):
            for warning in report['warnings']:
                st.text(warning)
    entries = execution_journal().proposals()
    cols = st.columns(3)
    for col, status in zip(cols, STATUSES):
        col.metric(status, sum(e['status'] == status for e in entries))
    project = st.selectbox('项目范围', ['level tunning'], key='review_project')
    filters = st.columns([1, 2])
    status = filters[0].selectbox('提案状态', ['全部']+STATUSES)
    keyword = filters[1].text_input('搜索标题、模块、提交人或提案编号')
    filtered = filter_proposals(entries, status, keyword)
    st.subheader('提案列表')
    st.caption('列表字段用于定位，正文和指纹以详情回读为准。当前不提供批量批准。')
    if not filtered:
        st.info('当前筛选下暂无提案。')
        return
    st.dataframe([{'标题':e.get('title') or '待回读标题', '项目':e.get('project') or project,
                   '模块':e.get('module',''), '提交人':e.get('submitted_by') or '管理员创建',
                   '提交时间':e.get('created_at',''), '状态':e['status'], '提案编号':e['id']}
                  for e in filtered], hide_index=True, use_container_width=True)
    selected = st.selectbox('选择提案查看详情', [e['id'] for e in filtered],
                            format_func=lambda k: next((e.get('title') or e['id'])+' · '+e['status'] for e in filtered if e['id']==k))
    entry = next(e for e in filtered if e['id'] == selected)
    st.link_button('在钉钉查看提案', entry['receipt']['url'])
    if st.session_state.get('review_loaded_id') != selected:
        for key in ('proposal_snapshot','proposal_receipt','approval_record','publication_receipts','review_notes'):
            st.session_state.pop(key, None)
    if st.button('回读并打开提案详情'):
        try:
            loaded = execution_journal().load_proposal(DocumentService(config, identity), entry, readonly=True)
            st.session_state['proposal_snapshot'] = loaded
            st.session_state['proposal_receipt'] = entry['receipt']
            st.session_state['review_loaded_id'] = selected
            for key in ('approval_record','publication_receipts','review_notes'):
                st.session_state.pop(key, None)
        except (WorkflowFailure, ConnectionFailure) as error:
            st.error(str(error) if isinstance(error, WorkflowFailure) else '详情读取失败：'+error.stage+'；'+str(error.code))
            return
    if st.session_state.get('review_loaded_id') == selected:
        render_detail(config, execution_journal, st.session_state.get('proposal_snapshot'), entry['status'])
    else:
        st.info('打开详情后显示完整正文、目标基线与审核操作。')

def render_detail(config, execution_journal, snapshot, status):
    if snapshot and verify(snapshot):
        st.subheader('提案详情')
        st.write('状态：' + status)
        st.write('编号：' + snapshot['id'])
        st.write('时间：' + snapshot['created_at'] + '（北京时间）')
        st.code(snapshot['sha256'], language=None)
        st.write('类型：' + snapshot['kind'] + '；范围：' + snapshot['action'])
        if snapshot.get('submitted_by'):
            st.write('提交身份：'+snapshot['submitted_by'])
            if snapshot.get('target_url'):
                st.link_button('查看本次目标正式文档', snapshot['target_url'])
                st.text('基线 SHA-256：' + snapshot['baseline_sha256'])
            st.write('请求 ID：'+snapshot.get('submission_request_id',''))
            st.write('文档 ID：'+str(snapshot.get('document_id') or '待登记')+'；基线修订：'+str(snapshot.get('base_revision_id') or '待核验'))
        st.text(snapshot['title'])
        st.text(snapshot['reason'])
        # Render untrusted document text literally: no remote image requests or HTML.
        st.text(snapshot['content'])
        if status != '待审核':
            st.info('此提案已进入执行或发布，详情只读，不可重复批准。')
            st.link_button('在钉钉查看原提案', st.session_state['proposal_receipt']['url'])
            return
        st.subheader('审核操作')
        notes = st.text_area('核对意见（不构成审批）', key='review_notes', max_chars=3000)
        st.download_button('下载提案 Markdown', export_markdown(snapshot),
            file_name=snapshot['id'] + '.md', mime='text/markdown')
        record = {'proposal': snapshot, 'review_notes': notes, 'approval': None, 'execution': None}
        st.download_button('下载提案与核对意见 JSON', json.dumps(record, ensure_ascii=False, indent=2),
            file_name=snapshot['id'] + '.json', mime='application/json')
        st.divider()
        st.caption('限 level tunning 单人试运行。先保存待审核提案，再批准创建独立版本；不覆盖旧文档。项目索引尚未自动更新。')
        active_identity = st.session_state.get('reviewer_identity')
        logged_in = bool(active_identity and active_identity.get('expires_at', 0) > time.time())
        if not logged_in:
            st.info('请先完成钉钉审核人登录，才能保存提案或批准发布。')
        if st.button('保存待审核提案到钉钉', disabled=not logged_in):
            try:
                with st.spinner('保存独立提案并回读验证…'):
                    service = DocumentService(config, active_identity)
                    st.session_state['proposal_receipt'] = archive(execution_journal(), service, snapshot)
                st.success('待审核提案已落库并通过回读。')
            except (WorkflowFailure, ConnectionFailure) as error:
                st.error(str(error) if isinstance(error, WorkflowFailure) else '落库失败：' + error.stage + '；' + str(error.code))
            except Exception:
                st.error('执行异常。请先核对知识库，不要重新生成同一提案来绕过操作记录。')
        receipt = st.session_state.get('proposal_receipt')
        if receipt:
            st.link_button('查看钉钉待审核提案', receipt['url'])
            st.warning('批准后将创建新文档，并分别保存人工批准和版本变更记录。请核对上方全部正文及修改范围。')
            with st.form('approve_form'):
                confirmed = st.checkbox('我已核对本提案全文，批准此内容创建独立文档')
                decision_notes = st.text_area('审核说明（可选）', max_chars=2000)
                approve_clicked = st.form_submit_button('批准并发布当前提案', disabled=not logged_in or bool(st.session_state.get('publication_receipts')))
            if approve_clicked:
                if not confirmed:
                    st.error('请先明确勾选全文审核确认。')
                else:
                    try:
                        # Only a logged-in human button action creates this record.
                        approval = st.session_state.get('approval_record')
                        if not approval:
                            approval = {'proposal_id': snapshot['id'], 'proposal_hash': snapshot['sha256'],
                                'reviewer': active_identity['union_id'], 'reviewer_name': active_identity['name'],
                                'approved_at': now(), 'notes': decision_notes, 'decision': '批准创建独立文档'}
                            st.session_state['approval_record'] = approval
                        with st.spinner('保存审核记录、发布正文并逐项回读…'):
                            service = DocumentService(config, active_identity)
                            st.session_state['publication_receipts'] = publish(execution_journal(), service,
                                snapshot, active_identity, approval, receipt)
                        st.success('发布及独立变更记录已回读通过；项目索引仍待更新。')
                        try:
                            st.session_state['backup_result'] = run_backup(service)
                            st.success('发布后备份验证完成。')
                        except (WorkflowFailure, ConnectionFailure):
                            st.warning('发布已完成，备份未完成。请在下方重新执行备份，不要重复批准发布。')
                    except (WorkflowFailure, ConnectionFailure) as error:
                        st.error(str(error) if isinstance(error, WorkflowFailure) else '发布中断：' + error.stage + '；' + str(error.code))
                        st.warning('可能已完成部分步骤。保留当前提案；同一提案再次执行会先核对已有回执，创建结果不确定时会停止。')
                    except Exception:
                        st.error('执行异常，可能存在部分落库。请核对知识库，不要改用新提案编号重复发布。')
        receipts = st.session_state.get('publication_receipts')
        if receipts:
            for kind, label in [('proposal', '提案记录'), ('approval', '人工批准记录'),
                                ('published', '发布文档'), ('change', '版本变更记录')]:
                st.link_button('查看' + label, receipts[kind]['url'])
            st.download_button('下载执行回执（请留存）', json.dumps(receipts, ensure_ascii=False, indent=2),
                file_name=snapshot['id'] + '-receipts.json', mime='application/json')
        st.caption('执行回执及签名恢复凭据已分别保存到钉钉；本地 SQLite 可从完整凭据重建。无签名、正文变化或不完整步骤将停止自动恢复执行，请人工核对。当前仍为单人试运行。')



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


