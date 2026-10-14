"""Atomic credit selection must not select the shared three-line host."""
import unittest
from unittest.mock import patch
from scripts import browser as b


class CreditRangeDispatchTests(unittest.TestCase):
    def test_diagnosis_records_exact_changes_and_never_types(self):
        elem=lambda x:{b.ELEMENT_KEY:x}
        before={'ok':True,'host':elem('host'),'target':elem('old'),
                'signature':[{'tag':'DIV','style':''}]}
        selected={**before,'target':elem('new'),
                  'signature':[{'tag':'DIV','style':'outline:none'}]}
        with patch.object(b,'call',side_effect=[before,{},selected]) as call:
            result=b.replace_credit_text('sid','leaf','host','文案｜部门','文案丨部门',diagnose_only=True)
        self.assertFalse(result['typed'])
        self.assertEqual(result['diagnosis']['before_signature'],before['signature'])
        self.assertTrue(result['diagnosis']['target_changed'])
        self.assertTrue(result['diagnosis']['signature_changed'])
        self.assertFalse(result['diagnosis']['host_changed'])
        self.assertFalse(any(c.args[1].endswith('/value') for c in call.call_args_list))

    def run_case(self, select_ok=True, style_ok=True):
        elem=lambda identifier:{b.ELEMENT_KEY:identifier}
        inspect={'ok':True,'host':elem('host'),'target':elem('leaf'),'signature':[{'tag':'SPAN','style':'color:red'}]}
        selected=dict(inspect) if select_ok else {'ok':False,'reason':'selection'}
        after={'ok':True,'signature':inspect['signature'] if style_ok else []}
        results=[inspect,{},selected,{},after]
        events=[]
        def call(method,path,payload=None):
            events.append((method,path,payload));return results.pop(0)
        with patch.object(b,'call',side_effect=call),patch.object(b,'observe',return_value={'url':'known'}):
            if not select_ok or not style_ok:
                with self.assertRaises(b.BrowserError):b.replace_credit_text('sid','leaf','host','文案｜部门','文案丨部门')
            else:self.assertTrue(b.replace_credit_text('sid','leaf','host','文案｜部门','文案丨部门')['replaced'])
        return events

    def test_only_replacement_keys_are_sent_without_select_all(self):
        events=self.run_case()
        values=[e[2] for e in events if e[1].endswith('/value')]
        self.assertEqual(values,[{'text':'文案丨部门'}])
        self.assertFalse(any(e[1].endswith('/actions') for e in events))

    def test_unverified_range_never_sends_text(self):
        events=self.run_case(select_ok=False)
        self.assertFalse(any(e[1].endswith('/value') for e in events))

    def test_inline_style_change_is_not_reported_as_success(self):
        events=self.run_case(style_ok=False)
        self.assertEqual(sum(e[1].endswith('/value') for e in events),1)

    def test_arbitrary_replacement_is_refused_before_click(self):
        with patch.object(b,'call') as call,self.assertRaises(b.BrowserError):
            b.replace_credit_text('sid','leaf','host','文案｜部门','文案丨另一部门')
        call.assert_not_called()

    def test_missing_or_ambiguous_target_stops_before_click(self):
        with patch.object(b,'call',return_value={'ok':False,'reason':'identity'}) as call,self.assertRaises(b.BrowserError):
            b.replace_credit_text('sid','leaf','host','文案｜部门','文案丨部门')
        self.assertEqual(call.call_count,1)

class CreditRangeJavaScriptTests(unittest.TestCase):
    def test_shared_host_range_and_guards_execute_real_bridge_script(self):
        import json,shutil,subprocess
        node=shutil.which('node')
        if not node:self.skipTest('Node not installed')
        harness=r'''
const assert=require('assert');
const script=SCRIPT;
const target={tagName:'SPAN',children:[],innerText:'文案｜部门',
 getBoundingClientRect:()=>({width:50,height:20}),getAttribute:()=>'',querySelectorAll:()=>[]};
const host={tagName:'DIV',innerText:'文案｜部门\n排版｜另一人\n审核｜第三人',isContentEditable:true,
 getBoundingClientRect:()=>({width:100,height:80}),hasAttribute:()=>true,
 getAttribute:k=>k==='contenteditable'?'true':'',contains:e=>e===target,
 querySelectorAll:()=>[target]};
let range=null;
const selection={rangeCount:0,removeAllRanges(){this.rangeCount=0},
 addRange(r){range=r;this.rangeCount=1},toString:()=>target.innerText,getRangeAt:()=>range};
global.document={querySelectorAll:s=>s==='leaf'?[target]:[host],activeElement:host,
 createRange:()=>({selectNodeContents(e){this.startContainer=e;this.endContainer=e;}})};
global.window={getSelection:()=>selection};
const declarations=()=>{const a=['font-size'];a.getPropertyValue=()=>'';a.getPropertyPriority=()=>'';return a;};
target.style=declarations();host.style=declarations();
let computedFont='14px';
global.getComputedStyle=()=>({display:'block',visibility:'visible',fontSize:computedFont});
const run=()=>new Function(script)('leaf','host','文案｜部门','select','文案丨部门');
assert.equal(run().ok,true);assert.strictEqual(range.startContainer,target);
assert.notStrictEqual(range.startContainer,host);
const before=run().signature;
target.style.getPropertyValue=()=> '14px';
assert.deepStrictEqual(run().signature,before); // only raw font-size representation changes
computedFont='15px';assert.notDeepStrictEqual(run().signature,before);computedFont='14px';
target.style.push('color');target.style.getPropertyValue=p=>p==='color'?'red':'14px';
assert.notDeepStrictEqual(run().signature,before);target.style.pop();
document.activeElement={};assert.equal(run().reason,'focus');document.activeElement=host;
host.innerText+='\n正文';assert.equal(run().reason,'host_text');host.innerText='文案｜部门';
target.innerText='文案｜另一部门';assert.equal(run().reason,'scope');
'''.replace('SCRIPT',json.dumps(b.CREDIT_RANGE_SCRIPT))
        result=subprocess.run([node,'-e',harness],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
