"""Controls-only producer/consumer: large bodies cannot overflow menu reads."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import browser, check_runtime, draft_workflow as d


class EditorControlsTests(unittest.TestCase):
    def test_controls_cannot_be_accepted_as_body(self):
        with self.assertRaises(check_runtime.Stop) as caught:
            d.content({'ui_only':True})
        self.assertEqual(caught.exception.code,'body_scope_incomplete')

    def test_controls_require_matching_editor_identity(self):
        class B:
            def editor_ui(self):return {'url':d.editor_url('456')}
        with self.assertRaises(check_runtime.Stop) as caught:
            d.editor_controls(B(),'123')
        self.assertEqual(caught.exception.code,'draft_identity_changed')

    def test_consumer_rejects_partial_or_unmarked_controls(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'view.json'
            class B(check_runtime.Browser):
                def command(self,*args):
                    self.args=args
                    return {'local_artifact':str(path)}
            with patch.object(check_runtime,'ROOT',Path(tmp).resolve()):
                b=B('local/controls')
                for value, code in [({'ui_only':True,'excluded_selector':'.tn-page-vessel',
                                     'nodes_truncated':True,'text_truncated':False},'truncated_page'),
                                    ({'nodes_truncated':False,'text_truncated':False},'editor_ui_unverified')]:
                    path.write_text(json.dumps(value))
                    with self.assertRaises(check_runtime.Stop) as caught:b.editor_ui()
                    self.assertEqual(caught.exception.code,code)
                path.write_text(json.dumps({'ui_only':True,'excluded_selector':'.tn-page-vessel',
                                            'nodes_truncated':False,'text_truncated':False}))
                self.assertTrue(b.editor_ui()['ui_only'])
                self.assertEqual(b.args[0],'inspect-editor-ui')

    @unittest.skipUnless(shutil.which('node'),'Node required for rendered controls contract')
    def test_actual_bridge_excludes_large_body_and_ancestor_text(self):
        program=r'''
const Node={ELEMENT_NODE:1,TEXT_NODE:3};
const make=(tag,parent,text='',cls='')=>{
 const e={nodeType:1,tagName:tag,parentElement:parent,children:[],id:'',attributes:[],
 childNodes:text?[{nodeType:3,textContent:text}]:[],isContentEditable:false,
 classList:{contains:c=>c===cls,[Symbol.iterator]:function*(){if(cls)yield cls;}},
 getAttribute:a=>a==='class'?cls:null,hasAttribute:()=>false,closest:()=>null,
 getBoundingClientRect:()=>({x:0,y:0,width:10,height:10})};
 e.contains=x=>x===e||e.children.some(c=>c.contains(x));
 e.querySelectorAll=()=>e.children.flatMap(c=>[c,...c.querySelectorAll('*')]);
 Object.defineProperty(e,'innerText',{get:()=>text+e.children.map(c=>c.innerText).join('')});
 if(parent)parent.children.push(e);return e;
};
const html=make('HTML',null),body=make('BODY',html),main=make('DIV',body);
make('SPAN',main,'更多');make('SPAN',main,'保存');
const vessel=make('SECTION',main,'SECRET_BODY','.tn-page-vessel');
vessel.classList={contains:c=>c==='tn-page-vessel',[Symbol.iterator]:function*(){yield 'tn-page-vessel';}};
vessel.getAttribute=a=>a==='class'?'tn-page-vessel':null;
for(let i=0;i<3100;i++)make('P',vessel,'protected');
const document={title:'editor',querySelectorAll:s=>s==='body'?[body]:s==='.tn-page-vessel'?[vessel]:[]};
const window={CSS:null};const location={href:'https://xiumi.us/studio/v5#/paper/for/123'};
const getComputedStyle=()=>({display:'block',visibility:'visible',backgroundImage:'none'});
const execute=new Function('document','window','Node','location','getComputedStyle',
 'return function(){'+__SCRIPT__+'}')(document,window,Node,location,getComputedStyle);
const whole=execute('body'),ui=execute('body',true);
process.stdout.write(JSON.stringify({wholeTruncated:whole.nodes_truncated,
 uiTruncated:ui.nodes_truncated,uiText:ui.text,uiTexts:ui.nodes.map(n=>n.rendered_text),
 uiOnly:ui.ui_only,count:ui.nodes.length,labels:ui.nodes.map(n=>n.own_text),
 vessels:ui.nodes.filter(n=>n.class==='tn-page-vessel').length}));
'''.replace('__SCRIPT__',json.dumps(browser.INSPECT_SCRIPT))
        result=subprocess.run([shutil.which('node'),'-e',program],capture_output=True,text=True,encoding='utf-8',check=True)
        value=json.loads(result.stdout)
        self.assertTrue(value['wholeTruncated'])
        self.assertFalse(value['uiTruncated'])
        self.assertTrue(value['uiOnly'])
        self.assertEqual(value['count'],4)
        self.assertEqual(value['vessels'],0)
        self.assertIn('更多',value['labels']);self.assertIn('保存',value['labels'])
        self.assertNotIn('SECRET_BODY',value['uiText'])
        self.assertFalse(any('protected' in s for s in value['uiTexts']))
