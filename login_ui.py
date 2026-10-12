import time
import streamlit as st
from login_flow import LoginRegistry, LoginFailure, authorization_url


@st.cache_resource
def login_registry(version=2):
    return LoginRegistry()


def login(config, purpose):
    registry = login_registry()
    key = purpose + '_identity'
    pending_key = purpose + '_login_pending'
    identity = st.session_state.get(key)
    if identity and identity.get('expires_at', 0) <= time.time():
        st.session_state.pop(key, None)
        identity = None
    if identity:
        st.success('已验证钉钉身份：' + identity['name'])
        if st.button('退出钉钉登录', key=purpose+'_logout'):
            st.session_state.pop(key, None)
            st.rerun()
        return identity
    st.caption('本人完成钉钉授权，Agent 不接触密码或扫码。保留当前页，登录后自动返回当前流程。')
    if st.button('开始钉钉登录', key=purpose+'_login'):
        previous = st.session_state.pop(pending_key, None)
        if previous:
            registry.cancel(*previous)
        st.session_state[pending_key] = registry.begin(purpose)

    @st.fragment(run_every='3s')
    def status():
        pending = st.session_state.get(pending_key)
        if not pending:
            return
        try:
            result = registry.poll(*pending)
            if result:
                st.session_state.pop(pending_key, None)
                if 'identity' in result:
                    st.session_state[key] = result['identity']
                    st.rerun()
                else:
                    st.error(result['error'])
            else:
                st.link_button('打开钉钉授权页面', authorization_url(config['client_id'], pending[0]))
        except LoginFailure as error:
            st.session_state.pop(pending_key, None)
            st.warning(str(error))
    status()
    return None
