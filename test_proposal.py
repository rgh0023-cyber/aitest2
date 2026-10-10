import unittest
from proposal import create_proposal, verify, export_markdown


class ProposalTests(unittest.TestCase):
    def proposal(self):
        return create_proposal('level tunning', '生成流程', '新版本', '正式文档新版本', '修正遗漏', '正文\n第二行')

    def test_exact_content_and_scope_bound_to_digest(self):
        value = self.proposal()
        self.assertTrue(verify(value))
        for field in ('content', 'module', 'action', 'status', 'reason'):
            changed = dict(value)
            changed[field] += '改动'
            self.assertFalse(verify(changed))
            with self.assertRaises(ValueError):
                export_markdown(changed)

    def test_independent_proposals_and_pending_status(self):
        first, second = self.proposal(), self.proposal()
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(first['status'], '待审核')
        self.assertIn(first['content'], export_markdown(first))
        self.assertIn('+08:00', first['created_at'])

    def test_empty_and_oversized_rejected(self):
        for text in (' ', '字' * 6001):
            with self.assertRaises(ValueError):
                create_proposal('p', 'm', 't', '会议记录', 'r', text)


if __name__ == '__main__':
    unittest.main()
