import hmac
import time

import streamlit as st

from dingtalk_client import ConnectionFailure, test_connection
from proposal import create_proposal, export_markdown, verify
import json
from login_flow import LoginRegistry, LoginFailure, authorization_url, verify_login, parse_callback

st.set_page_config(page_title="AI文档助手", page_icon="📄")
st.title("AI文档助手")
st.caption("第三阶段：钉钉登录身份验证与提案准备。正式审批及知识库写入尚未开放。")

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
st.header('创建提案')
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
    st.warning('知识库持久化及正式审批尚未开放。核对意见不会产生批准状态，也不会触发知识库写入。')
    st.button('批准并写入知识库（等待持久化接入）', disabled=True)
