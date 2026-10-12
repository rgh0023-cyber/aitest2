import unittest
from dashboard_model import project_members, filter_proposals


class DashboardTests(unittest.TestCase):
    def test_project_person_status_isolated_and_expiration_visible(self):
        entries=[dict(id='1',project_id='p1',union_id='u',name='User',action='request',created_at=1,expires_at=100),
                 dict(id='2',project_id='p1',union_id='u',name='User',action='grant',created_at=2,expires_at=5),
                 dict(id='3',project_id='p2',union_id='u',name='User',action='grant',created_at=3,expires_at=20)]
        result=project_members(entries,clock=10)
        self.assertEqual({r['project_id']:r['status'] for r in result}, {'p1':'已过期','p2':'已授权'})
        entries.append(dict(entries[1],id='4',action='revoke',created_at=4))
        self.assertEqual(project_members(entries,clock=10)[0]['status'],'已撤销')

    def test_filters_preserve_exact_status_and_selection_ids(self):
        entries=[dict(id='PR-a',title='规则调整',module='生成',submitted_by='Alice',status='待审核'),
                 dict(id='PR-b',title='规则调整',module='生成',submitted_by='Bob',status='已发布')]
        self.assertEqual([e['id'] for e in filter_proposals(entries,'待审核','规则')], ['PR-a'])
        self.assertEqual([e['id'] for e in filter_proposals(entries,'全部','BOB')], ['PR-b'])


if __name__=='__main__':
    unittest.main()
