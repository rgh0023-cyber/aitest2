import json
import time
import uuid
import streamlit as st
from project_access import authorize, request_access
from login_ui import login
from target_document import resolve
from daily_backup import configured_service
from document_workflow import Journal, WorkflowFailure
from submission_contract import validate, SubmissionFailure, PROJECT_ID
from dingtalk_client import ConnectionFailure


from submission_service import submit

def render(config):
    st.title('Agent 提案提交')
    st.caption('独立提交入口，只能向 level tunning 提交待审核提案。提交不代表批准或正式发布。')
    st.link_button('读取 ai文档核心skill','https://alidocs.dingtalk.com/i/nodes/dpYLaezmVNLda0ZjiKAY3ml38rMqPxX6')
    identity = login(config, 'submitter')
    if not identity:
        st.stop()
    if st.session_state.get('agent_current_subject') != identity['union_id']:
        for key in ('agent_submission_receipt','submission_status_result','agent_request_id','status_proposal_id'):
            st.session_state.pop(key,None)
        st.session_state['agent_current_subject']=identity['union_id']
    if st.query_params.get('page') == 'status':
        render_status(config,identity)
        return
    submit_tab,status_tab=st.tabs(['提交提案','查询审核状态'])
    with submit_tab:
        render_submit(config,identity)
    with status_tab:
        render_status(config,identity)


def render_submit(config,identity):
    if identity and st.button('申请 level tunning 提交权限'):
        try:
            request_access(configured_service(config), identity)
            st.success('接入申请已记录；管理员批准一次后即可提交，无须领取密码。')
        except WorkflowFailure as error:
            st.error('接入申请未完成：'+str(error))
        except ConnectionFailure as error:
            st.error('接入申请未确认：'+error.stage+'；'+str(error.code)+'。请管理员核对记录后再试。')
        except Exception:
            st.error('接入申请未确认成功。请管理员核对服务配置与接入记录，不要反复提交。')
    st.caption('新参与者需一次项目接入批准。登录只识别提交人，不授予审核权限。')
    uploaded=st.file_uploader('可选：上传提案 JSON',type=['json'])
    imported={}
    if uploaded:
        try:
            if uploaded.size>100000:
                raise ValueError('oversized')
            imported=json.loads(uploaded.getvalue().decode('utf-8-sig'))
            if not isinstance(imported,dict):
                raise ValueError('invalid')
        except (ValueError,UnicodeDecodeError):
            st.error('JSON 文件无效或超过 100 KB。');st.stop()
    if 'agent_request_id' not in st.session_state:
        st.session_state['agent_request_id']=str(uuid.uuid4())
    with st.form('agent_submission_form'):
        request_id=st.text_input('请求 ID（重试保留，内容变化须换新 ID）',value=imported.get('request_id',st.session_state['agent_request_id']))
        target_url=st.text_input('目标正式文档链接',value=imported.get('target_url',''),key='agent_target_url')
        module=st.text_input('模块或主题',value=imported.get('module',''),max_chars=200)
        title=st.text_input('提案标题',value=imported.get('title',''),max_chars=200)
        kinds=['正式文档新版本','修正日志','会议记录','问答记录']
        kind=st.selectbox('记录类型',kinds,index=kinds.index(imported.get('kind')) if imported.get('kind') in kinds else 0)
        reason=st.text_area('修改原因与基线说明',value=imported.get('reason',''),max_chars=2000)
        content=st.text_area('提案正文',value=imported.get('content',''),max_chars=6000,height=300)
        clicked=st.form_submit_button('提交到协作审核库')
    if clicked:
        if time.time()-st.session_state.get('last_submission_attempt',0)<10:
            st.warning('请间隔至少 10 秒再提交。')
        else:
            st.session_state['last_submission_attempt']=time.time()
            try:
                config=dict(st.secrets['dingtalk'])
                with st.spinner('验证提交权限、核对重复请求并保存提案…'):
                    service=configured_service(config)
                    principal=authorize(service,identity)
                    baseline=resolve(service,target_url)
                    payload=dict(imported) if imported else {}
                    payload.update(schema_version=1,request_id=request_id,project_id=PROJECT_ID,
                        module=module,title=title,kind=kind,reason=reason,content=content,
                        target_url=baseline['url'],document_id=baseline['document_id'],
                        baseline_sha256=baseline['content_sha256'])
                    validate(payload,principal)  # Always validate, including retries.
                    journal=Journal()
                    journal.sync_and_restore(service)
                    result=submit(journal,service,payload,principal)
                st.session_state['agent_submission_receipt']=result
                st.success('待审核提案已保存并通过回读验证。')
            except (WorkflowFailure,SubmissionFailure) as error:
                st.error(str(error))
            except ConnectionFailure as error:
                st.error('提交中断：'+error.stage+'；'+str(error.code)+'。保持请求 ID，先核对状态。')
            except Exception:
                st.error('提交未确认成功。保持请求 ID；请管理员核对，不要换 ID 盲目重复提交。')
    result=st.session_state.get('agent_submission_receipt')
    if result:
        st.write('提案编号：'+result['proposal_id'])
        st.link_button('查看协作审核库提案',result['review_library_url'])
        st.download_button('下载提交回执',json.dumps(result,ensure_ascii=False,indent=2),
            file_name=result['request_id']+'-receipt.json',mime='application/json')



def render_status(config,identity):
    from submission_status import lookup
    st.subheader('查询审核状态')
    st.caption('仅查询当前钉钉身份提交的提案。审核通过不一定是正式业务文档，结果会说明保存性质。')
    last=st.session_state.get('agent_submission_receipt',{}).get('proposal_id','')
    with st.form('submission_status_form'):
        proposal_id=st.text_input('提案编号',value=last,key='status_proposal_id')
        queried=st.form_submit_button('查询审核状态')
    if queried:
        st.session_state.pop('submission_status_result',None)
        try:
            with st.spinner('核验提案归属与审核记录…'):
                service=configured_service(config)
                journal=Journal()
                journal.sync_and_restore(service)
                st.session_state['submission_status_result']=lookup(journal,service,identity,proposal_id.strip())
        except WorkflowFailure as error:
            st.error(str(error))
        except ConnectionFailure as error:
            st.error('查询未完成：'+error.stage+'；'+str(error.code))
        except Exception:
            st.error('状态未确认，请保持编号，稍后核对。')
    result=st.session_state.get('submission_status_result')
    if result:
        st.write('审核状态：'+result['status'])
        st.write('内容性质：'+result['library_state'])
        st.link_button('查看原提案',result['review_library_url'])
        if result['decision_url']:
            st.link_button('查看审核决定',result['decision_url'])
        if result['formal_document_url']:
            st.link_button('查看已发布正式版本',result['formal_document_url'])
        elif result['record_url']:
            st.link_button('查看过程记录',result['record_url'])
        st.json(result)
