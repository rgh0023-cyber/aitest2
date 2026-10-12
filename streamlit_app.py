import time

import streamlit as st

from dingtalk_client import ConnectionFailure, test_connection
from login_flow import LoginFailure, verify_login, parse_callback
from document_workflow import Journal

st.set_page_config(page_title="AI文档助手", page_icon="📄")
@st.cache_resource
def execution_journal(schema_version=6):
    # Version cache entries so hot deployments do not retain obsolete classes.
    return Journal()

from login_ui import login_registry, login
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
        purpose = registry.claim(state)
        claimed = True
        if denied:
            registry.complete(state, {'error': '用户未完成授权，请重新发起登录。'})
            st.info('授权未完成。请返回发起登录的原页面。')
        else:
            identity = verify_login(dict(st.secrets['dingtalk']), code, purpose)
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

if st.query_params.get('page') == 'submit':
    from agent_page import render
    render(config)
    st.stop()

st.title('AI文档助手')
st.caption('提交、审核、正式发布分别授权；登录不代表批准。')
identity = login(config, 'reviewer')
if not identity:
    st.stop()

from access_ui import render as render_access
from review_ui import render_review, render_create, render_operations
permission_tab, review_tab, draft_tab, operations_tab = st.tabs(
    ['项目权限管理', '提案审核', '创建提案', '运行与备份'])
with permission_tab:
    render_access(config, identity)
with review_tab:
    render_review(config, identity, execution_journal)
with draft_tab:
    if st.session_state.get('cloud_recovery_v4'):
        render_create(config, identity, execution_journal)
    else:
        st.info('请先在提案审核 Tab 完成恢复校验，再创建提案。')
with operations_tab:
    render_operations(config, identity)
