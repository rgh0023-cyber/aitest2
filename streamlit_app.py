import hmac
import time

import streamlit as st

from dingtalk_client import ConnectionFailure, test_connection

st.set_page_config(page_title="AI文档助手", page_icon="📄")
st.title("AI文档助手")
st.caption("第一阶段：应用连接诊断。提案、审批和文档写入功能尚未开放。")

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
