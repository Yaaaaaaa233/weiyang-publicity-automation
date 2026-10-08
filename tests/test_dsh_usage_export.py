import unittest
from datetime import datetime, timezone, timedelta
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from dsh_usage_export import export

class UsageExport(unittest.TestCase):
    def setUp(self):
        self.start=datetime(2026,10,8,tzinfo=timezone.utc)
        self.ms=int(self.start.timestamp()*1000)
        self.events=[{'type':'session','id':'session-example'},
            {'type':'step/start','seq':1,'time':self.ms+1000,'data':{'turn':1,'step':1}},
            {'type':'assistant/message','seq':2,'time':self.ms+2000,'data':{'turn':1,'step':1,
              'message':{'id':'actual-message-id','source':{'model':'deepseek-flash'},'content':'secret-do-not-export'},
              'usage':{'inputTokens':10,'cacheReadTokens':20,'outputTokens':3,'cacheWriteTokens':0,'totalTokens':33}}},
            {'type':'turn/end','seq':3,'time':self.ms+4000,'data':{'turn':1}}]
    def run_export(self, events=None):
        return export(events or self.events,self.start,self.start+timedelta(seconds=10),{1})
    def test_duplicate_persisted_call(self):
        records,meta=self.run_export(self.events+[self.events[2]])
        self.assertEqual(len(records),1);self.assertNotIn('secret',str(records)+str(meta))
        self.assertFalse(meta['provider_request_id_available'])
    def test_conflicting_sequence(self):
        changed={**self.events[2],'time':self.ms+3000}
        with self.assertRaises(ValueError):self.run_export(self.events+[changed])
    def test_boundary_crossing(self):
        with self.assertRaises(ValueError):export(self.events,self.start+timedelta(seconds=1.5),self.start+timedelta(seconds=10),{1})
    def test_wrong_turn_excluded(self):
        records,meta=export(self.events,self.start,self.start+timedelta(seconds=10),{2})
        self.assertEqual(records,[]);self.assertEqual(meta['status'],'unknown')
    def test_compaction_is_explicit_partial(self):
        extra={'type':'compaction/summary','seq':4,'time':self.ms+3000,'data':{'usage':{'inputTokens':7}}}
        records,meta=self.run_export(self.events+[extra]);self.assertEqual(meta['status'],'partial')
        self.assertEqual(meta['unattributed_usage'][0]['seq'],4)
    def test_token_identity_change_rejected(self):
        self.events[2]['data']['usage']['totalTokens']=99
        with self.assertRaises(ValueError):self.run_export()
