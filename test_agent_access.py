import json
import hashlib
import time
import unittest
from unittest.mock import patch
from agent_access import grant, revoke, authenticate
from document_workflow import WorkflowFailure


class MemoryService:
    def __init__(self):
        self.nodes=[];self.text={}
    def seal(self,payload):
        return hashlib.sha256(('test-key'+json.dumps(payload,sort_keys=True)).encode()).hexdigest()
    def create(self,name,folder):
        node={'nodeId':str(len(self.nodes)+1),'name':name,'docKey':str(len(self.nodes)+1),'url':'test'}
        self.nodes.append(node);return node
    def ensure_content(self,receipt,text):
        self.text[receipt['nodeId']]=text
    def list_nodes(self,folder):
        return self.nodes
    def read(self,receipt):
        return [self.text[receipt['docKey']]]


class AgentAccessTests(unittest.TestCase):
    def test_scoped_grant_then_revocation(self):
        service=MemoryService()
        token,payload,_=grant(service,'测试参与者',1)
        self.assertNotIn(token,json.dumps(service.text))
        self.assertEqual(authenticate(service,token)['scope'],'proposal:submit')
        revoke(service,payload['credential_id'])
        with self.assertRaises(WorkflowFailure):authenticate(service,token)
    def test_expired_unknown_and_tampered_records_are_denied(self):
        service=MemoryService();token,_,_=grant(service,'测试',1)
        with patch('agent_access.time.time',return_value=time.time()+90000):
            with self.assertRaises(WorkflowFailure):authenticate(service,token)
        with self.assertRaises(WorkflowFailure):authenticate(service,'adt_'+'z'*43)
        service.text['1']=service.text['1'].replace('proposal:submit','publish')
        with self.assertRaises(WorkflowFailure):authenticate(service,token)


if __name__=='__main__':unittest.main()
