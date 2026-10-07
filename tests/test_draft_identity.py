import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from scripts import check_runtime, xiumi_scan
import unittest
from scripts.draft_identity import conflicts, validate_identity

class IdentityTests(unittest.TestCase):
    def item(self):
        return {'id':'a','evidence_paths':['local/body.json'],'body_checks':{
            k:{'result':'matched','reason':'Observed facts','evidence_paths':['local/body.json']}
            for k in ('topic','department','date','week','unique_version')}}
    def test_department_week_and_type_contradictions(self):
        a={'title':'未央体育丨第二周比赛预告','unit':'学生会体育部'}
        self.assertEqual(set(conflicts(a,{'title':'未央学习丨第三周比赛回顾'})),
                         {'different_week','different_article_type','different_department'})
        self.assertEqual(conflicts(a,{'title':'未央体育 | 第二周赛事预告'}),[])
    def test_unstructured_similarity_cannot_confirm(self):
        with self.assertRaises(ValueError):
            validate_identity({'identity_reason':'标题很相似'},[{'id':'a'}])
        item=self.item();item['body_checks']['date']['result']='unknown'
        with self.assertRaises(ValueError):validate_identity(item,[{'id':'a'}])
    def test_same_topic_with_different_title_can_pass_evidenced_checks(self):
        item=self.item();item['body_checks']['week']['result']='not_applicable'
        validate_identity(item,[{'id':'a'}])
        item['body_checks']['topic']['evidence_paths']=['local/unreferenced.json']
        with self.assertRaises(ValueError):validate_identity(item,[{'id':'a'}])
    def test_versions_require_review_of_all_and_actual_choice(self):
        item=self.item();candidates=[{'id':'a'},{'id':'b'}]
        with self.assertRaises(ValueError):validate_identity(item,candidates)
        item['candidate_reviews']=[{'id':i,'result':'same_manuscript','reason':'Same content, distinct layout','evidence_paths':['local/body.json']} for i in ('a','b')]
        with self.assertRaises(ValueError):validate_identity(item,candidates)
        item['version_choice']={'id':'a','reason':'Verified layout selection','evidence_paths':['local/body.json']}
        validate_identity(item,candidates)
        item['candidate_reviews'][1]['result']='different_manuscript'
        del item['version_choice'];validate_identity(item,candidates)

    def test_first_seen_is_preserved_and_initial_stock_not_received_today(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(check_runtime,'ROOT',Path(directory).resolve()):
            cards=[{'id':'a','display_date':'2026-09-25'}]
            xiumi_scan.record_inventory(cards,'test','2026-10-07T15:00:00+08:00')
            later=[{'id':'a','display_date':'2026-10-08'},{'id':'b','display_date':'2026-10-08'}]
            xiumi_scan.record_inventory(later,'test','2026-10-08T15:00:00+08:00')
            self.assertEqual(later[0]['first_seen_at'],'2026-10-07T15:00:00+08:00')
            self.assertTrue(later[0]['initial_inventory'])
            self.assertFalse(later[1]['initial_inventory'])
            inventory=json.loads(next((Path(directory)/'local/xiumi-inventory').glob('*.json')).read_text())
            self.assertEqual(inventory['cards']['a']['first_display_date'],'2026-09-25')
            self.assertEqual(inventory['cards']['a']['last_display_date'],'2026-10-08')
