"""Body acquisition recovery and producer/consumer path contract."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import browser as bridge, draft_workflow as d, check_runtime
from scripts.check_runtime import Stop


class PositionalPathContractTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node required for JavaScript path contract')
    def test_actual_javascript_path_feeds_scope_discovery(self):
        # Execute the actual producer, not a separately invented selector generator.
        producer = bridge.INSPECT_SCRIPT.split('const path = e => {', 1)[1].split(
            '\n};', 1)[0]
        program = r'''
const Node={ELEMENT_NODE:1};
const make=(tag,parent)=>{
  const e={nodeType:1,tagName:tag,parentElement:parent,children:[]};
  if(parent)parent.children.push(e);
  return e;
};
const html=make('HTML',null),body=make('BODY',html),main=make('MAIN',body);
make('DIV',main);
const group=make('DIV',main),vessel=make('SECTION',group);
const path=e=>{__PRODUCER__};
process.stdout.write(JSON.stringify({vessel:path(vessel),scope:path(main)}));
'''.replace('__PRODUCER__', producer)
        result = subprocess.run([shutil.which('node'), '-e', program],
                                capture_output=True, text=True, check=True)
        paths = json.loads(result.stdout)
        calls = []

        class Reader:
            def snapshot(self, selector='body'):
                calls.append(selector)
                if selector==d.TITLE:
                    return {'nodes':[{'tag':'input','placeholder':d.TITLE_PLACEHOLDER,
                                     'selector':paths['scope']+' > input:nth-of-type(1)'}]}
                nodes = [{'class':'tn-page-vessel','selector':paths['vessel']}]
                if selector == paths['scope']:
                    nodes.append({'tag':'input','placeholder':d.TITLE_PLACEHOLDER})
                return {'nodes':nodes}

        self.assertNotIn('tn-page-vessel', paths['vessel'])
        self.assertEqual(d.body_scope(Reader(), '123'), paths['scope'])
        self.assertNotIn('html:nth-of-type(1) > body:nth-of-type(1)', calls)
        self.assertEqual(calls, ['.tn-page-vessel',d.TITLE,paths['scope']])


class BodyWindowRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.folder = self.root/'local/body-evidence'
        self.folder.mkdir(parents=True)
        for module in (d, check_runtime):
            p = patch.object(module, 'ROOT', self.root)
            p.start(); self.addCleanup(p.stop)
        self.candidate = {'id':'123','url':d.editor_url('123'),'title':'测试正文'}

    def reader(self, url=None, cleanup_error=False):
        folder = self.folder

        class Reader:
            def __init__(self):
                self.folder=folder; self.evidence=[]; self.calls=[]

            def command(self, *args):
                self.calls.append(args)
                if args[0]=='new-window':
                    return {'handle':'created','previous_handle':'original'}
                if args[0]=='observe':return {'url':url or d.editor_url('123')}
                if args[0]=='close-window' and cleanup_error:
                    raise Stop('cleanup_failed','close refused')
                return {}

        return Reader()

    def test_collection_failure_closes_only_created_window_and_preserves_fault(self):
        b=self.reader(); original=Stop('truncated_page','incomplete')
        with patch.object(d,'snapshot_body',side_effect=original):
            with self.assertRaises(Stop) as caught:d.observe(b,self.candidate)
        self.assertIs(caught.exception,original)
        self.assertIn(('switch-window','created'),b.calls)
        self.assertEqual(b.calls[-1],('close-window','created','--expect-url',
                                     d.editor_url('123'),'--return-to','original'))

    def test_cleanup_failure_keeps_original_fault_and_records_evidence(self):
        b=self.reader(cleanup_error=True); original=Stop('body_truncated','incomplete')
        with patch.object(d,'snapshot_body',side_effect=original):
            with self.assertRaises(Stop) as caught:d.observe(b,self.candidate)
        self.assertIs(caught.exception,original)
        record=json.loads((self.root/b.evidence[0]).read_text())
        self.assertEqual(record['error'],'cleanup_failed')

    def test_redirected_window_is_kept_for_inspection(self):
        b=self.reader(url=d.editor_url('456'))
        with patch.object(d,'snapshot_body',side_effect=Stop('draft_identity_changed','changed')):
            with self.assertRaises(Stop):d.observe(b,self.candidate)
        self.assertFalse(any(c[0]=='close-window' for c in b.calls))

    def test_cleanup_record_write_failure_does_not_hide_collection_fault(self):
        b=self.reader(cleanup_error=True); original=Stop('body_truncated','incomplete')
        with patch.object(d,'snapshot_body',side_effect=original), \
             patch.object(d,'write',side_effect=OSError('disk full')):
            with self.assertRaises(Stop) as caught:d.observe(b,self.candidate)
        self.assertIs(caught.exception,original)

    def test_successful_collection_still_requires_verified_cleanup(self):
        b=self.reader(cleanup_error=True)
        with patch.object(d,'snapshot_body',return_value=({}, {'title':'测试正文'})):
            with self.assertRaises(Stop) as caught:d.observe(b,self.candidate)
        self.assertEqual(caught.exception.code,'cleanup_failed')
