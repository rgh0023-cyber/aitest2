import hmac
import time

import streamlit as st

from dingtalk_client import ConnectionFailure, test_connection
from proposal import create_proposal, export_markdown, verify
import json
from login_flow import LoginRegistry, LoginFailure, authorization_url, verify_login, parse_callback
from document_workflow import Journal, DocumentService, WorkflowFailure, archive, publish, now
from backup_service import run_backup
from agent_access import grant, revoke, credentials

st.set_page_config(page_title="AI文档助手", page_icon="📄")
if st.query_params.get('page') == 'submit':
    from agent_page import render
    render()
    st.stop()  # Submission credentials can never reach owner/reviewer controls.
st.title("AI文档助手")
st.caption("第四阶段：单人审批发布试运行。提案、审核、发布与变更记录分别落库。")
st.caption('level tunning：审核库保存过程，正式库保存批准版本，备份库保存最新完整副本与历史 diff。')

@st.cache_resource
def execution_journal(schema_version=5):
    # Version cache entries so hot deployments do not retain obsolete classes.
    return Journal()

@st.cache_resource
def login_registry():
    return LoginRegistry()

registry = login_registry()
# OAuth redirects open a fresh tab/session. Only the original tab can poll the
# result using a separate owner secret, which is never included in any URL.
if any(k in st.query_params for k in ('authCode', 'code', 'error')):
    states = st.query_params.get_all('state')
    auth_codes = st.query_params.get_all('authCode')
    codes = st.query_params.get_all('code')
    errors = st.query_params.get_all('error')
    st.query_params.clear()
    claimed = False
    try:
        state, code, denied = parse_callback(states, auth_codes, codes, errors)
        registry.claim(state)
        claimed = True
        if denied:
            registry.complete(state, {'error': '用户未完成授权，请重新发起登录。'})
            st.info('授权未完成。请返回发起登录的原页面。')
        else:
            identity = verify_login(dict(st.secrets['dingtalk']), code)
            registry.complete(state, {'identity': identity})
            st.success('身份验证完成。请返回发起登录的原页面，此标签页可以关闭。')
    except ConnectionFailure as error:
        message = '登录失败：' + error.stage + '；错误码：' + str(error.code)
        if claimed:
            try:
                registry.complete(state, {'error': message})
            except LoginFailure:
                pass
        st.error(message)
        if error.scopes:
            st.warning('需要权限：' + '、'.join(error.scopes))
    except LoginFailure as error:
        st.error(str(error))
    except Exception:
        if claimed:
            try:
                registry.complete(state, {'error': '登录处理失败，请重新发起。'})
            except LoginFailure:
                pass
        st.error('登录处理失败，请返回原页面重新发起。')
    st.stop()

# A public URL must not expose application-powered document queries.
try:
    access_password = st.secrets['access']['diagnostic_password']
except (KeyError, FileNotFoundError):
    st.warning('请先在 Secrets 中添加 [access] diagnostic_password，配置完成前诊断功能关闭。')
    st.stop()
if not isinstance(access_password, str) or len(access_password) < 16:
    st.warning('诊断访问密码至少需要 16 个字符。')
    st.stop()

def authenticate():
    value = st.session_state.get('diagnostic_login', '')
    st.session_state['diagnostic_authorized'] = hmac.compare_digest(
        value.encode('utf-8'), access_password.encode('utf-8'))
    st.session_state.pop('diagnostic_login', None)

if not st.session_state.get('diagnostic_authorized'):
    st.text_input('诊断访问密码', type='password', key='diagnostic_login', on_change=authenticate)
    if st.session_state.get('diagnostic_authorized') is False:
        st.error('密码不正确。')
    st.caption('仅用于单人试运行，不代表钉钉身份认证。')
    st.stop()
if st.button('退出诊断'):
    pending = st.session_state.get('login_pending')
    if pending:
        registry.cancel(*pending)
    st.session_state.clear()
    st.rerun()

try:
    config = dict(st.secrets['dingtalk'])
except (KeyError, FileNotFoundError):
    st.error('缺少 [dingtalk] 配置，请在应用 Secrets 中填写。')
    st.stop()
missing = [key for key in ('client_id', 'client_secret', 'workspace_id')
           if not isinstance(config.get(key), str) or not config[key].strip()]
if missing:
    st.error('缺少配置项：' + '、'.join(missing))
    st.stop()

st.header('钉钉审核人登录')
identity = st.session_state.get('reviewer_identity')
if identity and identity['expires_at'] <= time.time():
    st.session_state.pop('reviewer_identity', None)
    identity = None
if identity:
    st.success('已验证审核人：' + identity['name'])
    st.caption('身份有效期 30 分钟，仅当前会话有效。登录成功不等于批准任何提案。')
    if st.button('退出钉钉审核身份'):
        st.session_state.pop('reviewer_identity', None)
        st.rerun()
else:
    st.caption('点击后在新标签页授权；请保留当前页面。仅允许服务配置的操作人登录。')
    if st.button('准备钉钉登录'):
        previous = st.session_state.pop('login_pending', None)
        if previous:
            registry.cancel(*previous)
        try:
            st.session_state['login_pending'] = registry.begin()
        except LoginFailure as error:
            st.error(str(error))

    @st.fragment(run_every='3s')
    def login_status():
        pending = st.session_state.get('login_pending')
        if not pending:
            return
        try:
            result = registry.poll(*pending)
        except LoginFailure as error:
            st.session_state.pop('login_pending', None)
            st.warning(str(error))
            return
        if result:
            st.session_state.pop('login_pending', None)
            if 'identity' in result:
                st.session_state['reviewer_identity'] = result['identity']
                st.rerun()
            else:
                st.error(result['error'])
        else:
            st.link_button('打开钉钉授权页面', authorization_url(config['client_id'], pending[0]))
            st.caption('等待授权结果，5 分钟后过期。完成后回到本页面自动显示身份。')
    login_status()

if st.session_state.get('reviewer_identity',{}).get('expires_at',0)>time.time():
    with st.expander('管理其他 Agent 的提交权限'):
        st.caption('每个凭据仅允许向 level tunning 提交待审材料。身份指凭据对应标识，不代替参与者本人的钉钉登录验证。凭据原文只显示给你，钉钉只存哈希与签名；妥善私下交给对应参与者。')
        st.link_button('其他 Agent 的提交页面','https://aitest-kltimq4ak9guq9eijbhz3j.streamlit.app/?page=submit')
        with st.form('issue_agent_credential'):
            label=st.text_input('参与者或 Agent 标识',max_chars=80)
            days=st.number_input('有效天数',min_value=1,max_value=30,value=7)
            issue=st.form_submit_button('创建仅提交凭据')
        if issue:
            try:
                access_service=DocumentService(config,st.session_state['reviewer_identity'])
                token,payload,receipt=grant(access_service,label,int(days))
                st.session_state['new_agent_grant']={'token':token,'label':payload['label'],'id':payload['credential_id']}
                st.success('已创建并回读验证，仅具备提案提交权限。')
            except (WorkflowFailure,ConnectionFailure):
                st.error('创建未确认成功，请检查审核库接入记录，勿重复创建。')
        new_grant=st.session_state.get('new_agent_grant')
        if new_grant:
            st.write('凭据对应：'+new_grant['label'])
            st.code(new_grant['token'],language=None)
            if st.button('我已保存，隐藏凭据'):
                st.session_state.pop('new_agent_grant',None)
                st.rerun()
        if st.button('读取提交凭据列表'):
            try:
                st.session_state['agent_grants']=credentials(DocumentService(config,st.session_state['reviewer_identity']))
            except (WorkflowFailure,ConnectionFailure):
                st.error('无法核验接入记录。')
        grants=st.session_state.get('agent_grants',[])
        if grants:
            active=[item for item in grants if not item['revoked'] and item['expires_at']>time.time()]
            st.table([{'标识':item['label'],'权限':'仅提交','状态':'已撤销' if item['revoked'] else '有效' if item['expires_at']>time.time() else '已过期'} for item in grants])
            if active:
                chosen=st.selectbox('要撤销的提交凭据',[item['credential_id'] for item in active],
                    format_func=lambda key:next(item['label'] for item in active if item['credential_id']==key))
                if st.button('撤销所选提交凭据'):
                    try:
                        revoke(DocumentService(config,st.session_state['reviewer_identity']),chosen)
                        st.session_state.pop('agent_grants',None)
                        st.success('已撤销并回读验证；之后的提交会拒绝此凭据。')
                    except (WorkflowFailure,ConnectionFailure):
                        st.error('撤销未确认成功，请检查接入记录。')

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
st.header('已保存提案')
st.caption('登录后自动从钉钉扫描并校验恢复凭据，展示最近 100 条提案。恢复只重建列表与执行状态，不自动批准或发布。')
list_identity = st.session_state.get('reviewer_identity')
if list_identity and list_identity.get('expires_at', 0) > time.time():
    if not st.session_state.get('cloud_recovery_v3'):
        try:
            with st.spinner('从钉钉校验并恢复提案和执行状态…'):
                recovery_service = DocumentService(config, list_identity)
                report = execution_journal().sync_and_restore(recovery_service)
            st.session_state['cloud_recovery_v3'] = report
        except (WorkflowFailure, ConnectionFailure) as error:
            st.error(str(error) if isinstance(error, WorkflowFailure) else '恢复失败：' + error.stage + '；' + str(error.code))
            st.info('恢复未完整完成，暂不开放写入。请核对后重试。')
            st.stop()
        except Exception:
            st.error('恢复异常，写入暂时关闭。')
            st.stop()
    report = st.session_state['cloud_recovery_v3']
    st.caption('已校验恢复 ' + str(report['restored']) + ' 个执行步骤。')
    if report['warnings']:
        with st.expander('恢复中需要核对的记录'):
            for warning in report['warnings']:
                st.text(warning)
    entries = execution_journal().proposals()
    if entries:
        labels = {entry['id']: entry['status'] + '｜' + entry['id'] for entry in entries}
        selected = st.selectbox('选择已保存提案', [entry['id'] for entry in entries], format_func=lambda value: labels[value])
        selected_entry = next(entry for entry in entries if entry['id'] == selected)
        st.link_button('在钉钉查看所选提案', selected_entry['receipt']['url'])
        if st.button('从钉钉加载待审核提案', disabled=selected_entry['status'] != '待审核'):
            try:
                with st.spinner('回读正文并校验初次落库指纹…'):
                    service = DocumentService(config, list_identity)
                    loaded = execution_journal().load_proposal(service, selected_entry)
                st.session_state['proposal_snapshot'] = loaded
                st.session_state['proposal_receipt'] = selected_entry['receipt']
                for key in ('approval_record', 'publication_receipts', 'review_notes'):
                    st.session_state.pop(key, None)
                st.success('已从钉钉加载，向下查看待审核提案及审批入口。')
            except (WorkflowFailure, ConnectionFailure) as error:
                st.error(str(error) if isinstance(error, WorkflowFailure) else '读取失败：' + error.stage + '；' + str(error.code))
            except Exception:
                st.error('读取异常，提案未加载。')
    else:
        st.info('当前服务暂无可定位的已保存提案。')
    if st.button('重新扫描钉钉并刷新列表'):
        st.session_state.pop('cloud_recovery_v3', None)
        st.rerun()
else:
    st.info('完成钉钉登录后，可查看和加载已保存的提案。')

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
        st.session_state['proposal_snapshot'] = snapshot
        st.session_state.pop('review_notes', None)
        for key in ('proposal_receipt', 'approval_record', 'publication_receipts'):
            st.session_state.pop(key, None)
        st.success('已生成待审核快照。')
    except ValueError as error:
        st.error(str(error))
snapshot = st.session_state.get('proposal_snapshot')
if snapshot and verify(snapshot):
    st.header('待审核提案')
    st.write('编号：' + snapshot['id'])
    st.write('时间：' + snapshot['created_at'] + '（北京时间）')
    st.code(snapshot['sha256'], language=None)
    st.write('类型：' + snapshot['kind'] + '；范围：' + snapshot['action'])
    if snapshot.get('submitted_by'):
        st.write('提交身份（独立 Agent 凭据）：'+snapshot['submitted_by'])
        st.write('请求 ID：'+snapshot.get('submission_request_id',''))
        st.write('文档 ID：'+str(snapshot.get('document_id') or '待登记')+'；基线修订：'+str(snapshot.get('base_revision_id') or '待核验'))
    st.text(snapshot['title'])
    st.text(snapshot['reason'])
    # Render untrusted document text literally: no remote image requests or HTML.
    st.text(snapshot['content'])
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

st.divider()
st.header('level tunning 备份')
st.caption('最新完整副本覆盖更新；历史 diff 长期保留。先保存并验证 diff，再覆盖副本。每日云端任务独立于本页面运行。')
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
