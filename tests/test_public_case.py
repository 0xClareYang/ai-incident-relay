import tempfile
import unittest
from pathlib import Path
import relay


class PublicCaseTests(unittest.TestCase):
    def test_shipped_case_is_self_contained(self):
        base=relay.ROOT/'cases/hf-demo-public'
        data=relay.read_json(base/'ledger.json')
        self.assertEqual(relay.validate(data,base),[])
        self.assertEqual(data['run']['human_review']['status'],'pending')
        for source in data['sources']:
            self.assertEqual(source['access_status'],'partial')
            text=relay.contained(base,source['snapshot']['path']).read_bytes().decode('utf-8')
            self.assertLessEqual(len(text.split()),25)

    def test_export_can_revalidate_away_from_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'export'
            self.assertTrue(relay.export(relay.ROOT/'cases/hf-demo-public/ledger.json',output))
            self.assertEqual(relay.validate(relay.read_json(output/'ledger.json'),output),[])


if __name__=='__main__':
    unittest.main()
