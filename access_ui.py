import streamlit as st
from project_access import records, write
from document_workflow import DocumentService, WorkflowFailure
from dingtalk_client import ConnectionFailure
from dashboard_model import project_members, beijing_time


def render(config, identity):
    st.header('项目权限管理')
    st.caption('批准项目接入仅允许提交修改提案；正式文档仍需逐份审核后发布。')
    st.selectbox('项目', ['level tunning'], key='access_project')
    st.link_button('参与者提交入口', 'https://aitest-kltimq4ak9guq9eijbhz3j.streamlit.app/?page=submit')
    refresh = st.button('刷新权限申请', key='access_refresh')
    if refresh or 'project_access_records' not in st.session_state:
        try:
            with st.spinner('读取并校验钉钉项目接入记录…'):
                st.session_state['project_access_records'] = records(DocumentService(config, identity))
        except (WorkflowFailure, ConnectionFailure):
            st.session_state.pop('project_access_records', None)
            st.error('接入记录核验失败，权限操作暂时关闭。')
            return
    items = st.session_state.get('project_access_records', [])
    members = project_members(items)
    cols = st.columns(3)
    for col, status in zip(cols, ['待批准', '已授权', '已过期']):
        col.metric(status, sum(m['status'] == status for m in members))
    status = st.selectbox('权限状态', ['全部', '待批准', '已授权', '已撤销', '已过期'])
    filtered = [m for m in members if status == '全部' or m['status'] == status]
    if not filtered:
        st.info('当前筛选下暂无参与者。新参与者在提交页登录后申请项目接入。')
        return
    st.dataframe([{'参与者':m['name'], '项目':'level tunning', '权限':'仅提交修改提案',
                   '状态':m['status'], '申请或变更时间':beijing_time(m['created_at']),
                   '授权到期':beijing_time(m['expires_at']) if m['action']=='grant' else '—'}
                  for m in filtered], hide_index=True, use_container_width=True)
    chosen = st.selectbox('选择参与者查看权限详情', [m['union_id'] for m in filtered],
                         format_func=lambda k: next(m['name']+' · '+m['status']+' · '+k[-6:] for m in filtered if m['union_id']==k))
    item = next(m for m in filtered if m['union_id'] == chosen)
    st.subheader('权限详情与处理')
    st.write('参与者：'+item['name']+'；项目：level tunning；状态：'+item['status'])
    st.caption('以下操作仅调整此人的项目提交权限，不授予正式库编辑或审核权限。')
    with st.form('project_permission_decision'):
        days = st.number_input('授权有效天数', min_value=1, max_value=365, value=30)
        allow = st.form_submit_button('批准接入 / 续期')
        deny = st.form_submit_button('撤销提交权限', disabled=item['status']=='已撤销')
    if allow or deny:
        try:
            write(DocumentService(config, identity), item, 'grant' if allow else 'revoke', int(days))
            st.session_state['project_access_records'] = records(DocumentService(config, identity))
            st.session_state['access_notice'] = '权限变更已保存并回读验证。'
            st.rerun()
        except (WorkflowFailure, ConnectionFailure):
            st.error('权限变更未确认成功，请刷新核对记录后再操作。')
    notice = st.session_state.pop('access_notice', None)
    if notice:
        st.success(notice)
    with st.expander('此参与者的权限变更历史'):
        for event in sorted((r for r in items if r['union_id']==chosen), key=lambda r:r['created_at'], reverse=True):
            st.text(beijing_time(event['created_at'])+' · '+{'request':'申请接入','grant':'授权','revoke':'撤销'}[event['action']])
