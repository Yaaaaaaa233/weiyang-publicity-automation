"""Failure cases for the read-only preflight; no browser or account mutations."""
import copy
import unittest
from scripts.check_sync_composition import check


def inputs():
    plan = {'date': '2026-01-01', 'target_account': '测试公众号', 'prior_sync_attempts': 0,
            'articles': [{'row': i, 'is_headline': i == 2, 'sync_title': '测试稿' + str(i)}
                         for i in (1, 2, 3)]}
    nodes = [{'tag': 'div', 'class': 'x3-package', 'selector': 'body > main'}]
    backgrounds = []
    for i, source_row in enumerate((2, 1, 3)):
        path = 'body > main > div:nth-of-type(' + str(i + 1) + ')'
        nodes.extend([{'tag': 'div', 'class': 'x3-slice-plate', 'selector': path, 'rect': {'y': i * 100}},
                      {'tag': 'input', 'class': 'inner', 'selector': path + ' > input',
                       'value': '测试稿' + str(source_row)}])
        backgrounds.append({'class': 'bg-image', 'selector': path + ' > div',
                            'image': 'url(https://example.com/test.png)', 'rect': {'width': 100, 'height': 100}})
    nodes.extend([{'tag': 'label', 'class': 'wx-username', 'selector': 'body > header > label', 'own_text': '测试公众号'},
                  {'tag': 'input', 'class': 'preview-check', 'selector': 'body > header > input', 'checked': False},
                  {'tag': 'input', 'class': 'create-new-check', 'selector': 'body > header > input:nth-of-type(2)', 'checked': False}])
    return {'url': 'https://xiumi.us/studio/v5', 'text': '测试组合', 'nodes': nodes,
            'backgrounds': backgrounds, 'nodes_truncated': False, 'text_truncated': False}, plan


class PreflightTests(unittest.TestCase):
    def test_headline_is_promoted_and_other_rows_stay_ordered(self):
        snapshot, plan = inputs()
        result = check(snapshot, plan)
        self.assertEqual(result['expected_rows'], [2, 1, 3])
        self.assertTrue(result['ready_for_supervised_submission_review'])

    def test_wrong_visible_order_is_rejected(self):
        snapshot, plan = inputs()
        snapshot['nodes'][1]['rect']['y'], snapshot['nodes'][3]['rect']['y'] = 100, 0
        self.assertFalse(check(snapshot, plan)['composition_checks_passed'])

    def test_missing_cover_is_rejected(self):
        snapshot, plan = inputs()
        snapshot['backgrounds'].pop()
        self.assertFalse(check(snapshot, plan)['composition_checks_passed'])

    def test_wrong_or_multiple_target_is_rejected(self):
        snapshot, plan = inputs()
        target = next(n for n in snapshot['nodes'] if n['class'] == 'wx-username')
        target['own_text'] = '其他账号'
        self.assertFalse(check(snapshot, plan)['composition_checks_passed'])
        target['own_text'] = plan['target_account']
        snapshot['nodes'].append(copy.deepcopy(target))
        self.assertFalse(check(snapshot, plan)['composition_checks_passed'])

    def test_preview_and_unknown_checkbox_state_are_rejected(self):
        snapshot, plan = inputs()
        option = next(n for n in snapshot['nodes'] if n['class'] == 'preview-check')
        option['checked'] = True
        self.assertFalse(check(snapshot, plan)['composition_checks_passed'])
        option.pop('checked')
        self.assertFalse(check(snapshot, plan)['composition_checks_passed'])

    def test_truncated_capture_is_rejected(self):
        snapshot, plan = inputs()
        snapshot['nodes_truncated'] = True
        self.assertFalse(check(snapshot, plan)['composition_checks_passed'])

    def test_unmarked_day_is_accepted_in_sheet_order(self):
        """No marked headline is legal: the composition must read top-to-bottom.

        `inputs()` always promotes a headline, so the snapshot is rebuilt here with
        the plates in plain row order -- exactly what the approved fallback produces.
        """
        snapshot, plan = inputs()
        for article in plan['articles']:
            article['is_headline'] = False
        plates = [n for n in snapshot['nodes'] if n.get('class') == 'x3-slice-plate']
        rows = [r['row'] for r in sorted(plan['articles'], key=lambda a: a['row'])]
        for plate, row in zip(plates, rows):
            title = next(n for n in snapshot['nodes']
                         if n.get('class') == 'inner' and n['selector'].startswith(plate['selector'] + ' > '))
            title['value'] = '测试稿' + str(row)
        result = check(snapshot, plan)
        self.assertTrue(result['composition_checks_passed'])
        self.assertEqual(result['expected_rows'], [1, 2, 3])
        self.assertTrue(next(c for c in result['checks']
                             if c['name'] == 'at_most_one_confirmed_headline')['passed'])

    def test_multiple_headlines_are_rejected(self):
        snapshot, plan = inputs()
        for article in plan['articles']:
            article['is_headline'] = True
        result = check(snapshot, plan)
        self.assertFalse(result['composition_checks_passed'])
        self.assertFalse(next(c for c in result['checks']
                              if c['name'] == 'at_most_one_confirmed_headline')['passed'])

    def test_prior_attempt_does_not_allow_replay(self):
        snapshot, plan = inputs()
        plan['prior_sync_attempts'] = 1
        result = check(snapshot, plan)
        self.assertTrue(result['composition_checks_passed'])
        self.assertFalse(result['ready_for_supervised_submission_review'])

    def test_unrelated_library_content_is_not_a_selected_article(self):
        snapshot, plan = inputs()
        snapshot['nodes'].append({'tag': 'input', 'class': 'inner', 'selector': 'body > aside > input', 'value': '不相关稿件'})
        self.assertTrue(check(snapshot, plan)['composition_checks_passed'])

    def test_duplicate_titles_and_unconfirmed_headline_are_invalid(self):
        snapshot, plan = inputs()
        plan['articles'][0]['sync_title'] = plan['articles'][1]['sync_title']
        with self.assertRaises(ValueError):
            check(snapshot, plan)
        snapshot, plan = inputs()
        plan['articles'][0]['is_headline'] = None
        with self.assertRaises(ValueError):
            check(snapshot, plan)


if __name__ == '__main__':
    unittest.main()
