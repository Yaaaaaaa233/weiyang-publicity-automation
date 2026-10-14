"""Global-format UI must not turn removable trailing blanks into body edits."""
import copy
import unittest
from scripts import draft_workflow as d
from scripts.check_runtime import Stop


def sample(text='正文\n\n', control='基础格式：字号 16'):
    return {'url':d.editor_url('123'),'nodes_truncated':False,'text_truncated':False,
            'nodes':[{'class':'tn-page-vessel','selector':'v','rendered_text':text+control,
                      'rect':{'x':0,'y':0,'width':100,'height':100}},
                     {'tag':'input','placeholder':'请输入图文的标题','value':'标题','selector':'title'},
                     {'class':'tn-comp-top-level','selector':'v > c'},
                     {'tag':'p','class':'','selector':'v > c > p','paragraph_text':'正文','inline_style':''},
                     {'tag':'div','class':'global-format','selector':'v > format','rendered_text':control}],
            'images':[]}


class BodyControlBoundaryTests(unittest.TestCase):
    def test_only_observed_ui_suffix_is_removed(self):
        value=d.content(sample('正文：基础格式：字号 16\n\n'))
        self.assertEqual(value['text'],'正文：基础格式：字号 16\n\n')
        self.assertEqual(value['editor_text_suffix'],'基础格式：字号 16')

    def test_ui_inside_article_component_is_not_removed(self):
        snap=sample();snap['nodes'][-1]['selector']='v > c > format'
        with self.assertRaises(Stop):d.content(snap)

    def test_duplicate_or_non_suffix_control_stops(self):
        for mode in ['duplicate','non_suffix','truncated']:
            snap=sample()
            if mode=='duplicate':snap['nodes'].append(copy.deepcopy(snap['nodes'][-1]))
            if mode=='non_suffix':snap['nodes'][0]['rendered_text']+='正文'
            if mode=='truncated':snap['nodes'][-1]['rendered_text_truncated']=True
            with self.assertRaises(Stop):d.content(snap)

    def test_old_baseline_is_compared_without_mutating_it(self):
        old=d.content(sample());old['text']+='基础格式：字号 16';old.pop('editor_text_suffix')
        saved=copy.deepcopy(old);after=d.content(sample('正文\n'))
        self.assertTrue(d.preservation(old,after,{}));self.assertEqual(old,saved)

    def test_old_unproven_control_cannot_be_stripped(self):
        old=d.content(sample());old.pop('editor_text_suffix')
        with self.assertRaises(Stop):d.preservation(old,d.content(sample()),{})

    def test_inner_blank_lines_and_nonparagraph_text_are_protected(self):
        before=d.content(sample('正文\n\n第二段\n'))
        for text in ['正文\n第二段\n','正文\n\n另一段\n']:
            self.assertFalse(d.preservation(before,d.content(sample(text)),{}))

    def test_image_style_and_inner_empty_paragraph_changes_are_protected(self):
        before=d.content(sample());before['paragraphs']=[{'text':'一','style':''},{'text':'','style':''},{'text':'二','style':''}]
        for field,value in [('images',[{'src':'new'}]),('paragraphs',[{'text':'一','style':''},{'text':'二','style':''}]),('paragraphs',[{'text':'一','style':'color:red'},{'text':'','style':''},{'text':'二','style':''}])]:
            after=copy.deepcopy(before);after[field]=value
            self.assertFalse(d.preservation(before,after,{}))
