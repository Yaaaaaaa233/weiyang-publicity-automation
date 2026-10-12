"""The 3000-element snapshot cap: the fault that stopped the 2026-10-12 18:10 round.

`browser.py`'s INSPECT_SCRIPT caps a snapshot at 3000 visible elements and reports
`nodes_truncated`; `check_runtime.Browser.snapshot()` fails closed on that flag. That is correct
behaviour -- partial DOM must never be mistaken for the whole article -- but it had NO test, so
the limit was only discovered in production, on a 合辑稿 whose editor page exceeded the cap.

These cases pin three things:
  1. a truncated snapshot is always refused, with the right stop code (the missing regression);
  2. a complete snapshot still passes through unchanged;
  3. the read can be SCOPED to a selector, which is what makes the fix repo-side only.

`browser.py` also caps whole-page `text` at 100000 chars and per-node `rendered_text` at 12000,
and `draft_workflow.content()` has its own truncation guards; those are covered here as well so
the next wall is not found in production either.
"""
import json
import pathlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import check_runtime, draft_workflow as d
from scripts.check_runtime import Stop


def snapshot(nodes=(), **overrides):
    base = {'selector': 'body', 'url': 'https://xiumi.us/studio/v5#/paper/for/730830179',
            'title': '示例', 'text': 'x' * 10, 'text_truncated': False,
            'nodes': list(nodes), 'nodes_truncated': False, 'images': [], 'backgrounds': []}
    base.update(overrides)
    return base


class FakeBrowser(check_runtime.Browser):
    """A Browser whose one bridge command writes a real artifact and returns its path."""

    def __init__(self, payloads):
        super().__init__('local/evidence')
        self.payloads = payloads
        self.selectors = []

    def command(self, *args):
        assert args[0] == 'inspect', args[0]
        selector, name = args[1], args[2]
        self.selectors.append(selector)
        payload = self.payloads[selector]
        path = Path(self.folder) / name
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        return {'local_artifact': str(path)}


class BodyScopeDerivationTests(unittest.TestCase):
    """`body_scope` climbs from the vessel's parent until the TITLE INPUT is also in scope.

    Two separate subtrees must be covered: the body vessel and the title input. The vessel's parent
    was measured at 2204 elements WITHOUT the title (content() then raises draft_title_missing)
    while the shared ancestor is 2250 WITH it. This logic took several off-by-one attempts, so the
    exact expected selector is pinned here.

    `title_ancestor_parts` is the number of path segments of the ancestor that holds the title:
    parts[:title_ancestor_parts] is the shallowest ancestor that contains it.
    """

    class Probe:
        def __init__(self, vessel_selector, title_ancestor_parts=None, truncated=()):
            self.vessel_selector = vessel_selector
            self.title_ancestor_parts = title_ancestor_parts
            self.truncated = set(truncated)
            self.read = []

        def snapshot(self, frame=None, selector='body'):
            self.read.append(selector)
            if selector in self.truncated:
                raise Stop('truncated_page', 'too big')
            parts = [p for p in self.vessel_selector.split(' > ') if p]

            def in_scope(node_selector):
                # Two ways the bridge can be addressed, both modelled here:
                #   * a class selector ('.tn-page-vessel') identifies the vessel node itself, and
                #     is NOT a path prefix of the vessel's full path -- `.tn-page-vessel` is not a
                #     prefix of `body > a > b > c > .tn-page-vessel`;
                #   * a path selector matches every node whose path lies under it.
                if selector == self.vessel_selector or selector.startswith('.'):
                    return node_selector == self.vessel_selector
                if selector == 'body':
                    return True
                return node_selector.startswith(selector)

            nodes = []
            for depth in range(1, len(parts) + 1):
                ancestor = ' > '.join(parts[:depth])
                if ancestor == self.vessel_selector and in_scope(ancestor):
                    nodes.append({'tag': 'div', 'class': 'tn-page-vessel', 'selector': ancestor})
            if (self.title_ancestor_parts is not None
                    and self.title_ancestor_parts <= len(parts)):
                holder = ' > '.join(parts[:self.title_ancestor_parts])
                if in_scope(holder):
                    nodes.append({'tag': 'input', 'placeholder': d.TITLE_PLACEHOLDER,
                                  'selector': holder + ' > input'})
            return {'nodes': nodes}

    def scope(self, selector, title_ancestor_parts, truncated=()):
        probe = self.Probe(selector, title_ancestor_parts, truncated)
        return d.body_scope(probe, '730830179'), probe

    def test_climbs_until_the_title_input_is_in_scope(self):
        # vessel at depth 5, title held by the ancestor at depth 3 -> answer is depth 3.
        scope, probe = self.scope('body > a > b > c > .tn-page-vessel', title_ancestor_parts=3)
        self.assertEqual(scope, 'body > a > b')
        self.assertEqual(probe.read[0], '.tn-page-vessel', 'the vessel probe comes first')

    def test_stops_at_the_deepest_ancestor_that_works(self):
        # title held by the ancestor at depth 4 -> no need to climb to 3 or 2.
        scope, _ = self.scope('body > a > b > c > .tn-page-vessel', title_ancestor_parts=4)
        self.assertEqual(scope, 'body > a > b > c')

    def test_real_bridge_positional_path_has_no_class_names(self):
        path = ('html:nth-of-type(1) > body:nth-of-type(1) > '
                'main:nth-of-type(1) > div:nth-of-type(2) > section:nth-of-type(1)')
        scope, probe = self.scope(path, title_ancestor_parts=3)
        self.assertEqual(scope, 'html:nth-of-type(1) > body:nth-of-type(1) > main:nth-of-type(1)')
        self.assertNotIn('tn-page-vessel', path)
        self.assertGreater(len(probe.read), 1)

    def test_real_bridge_body_ancestor_is_not_read(self):
        path = 'html:nth-of-type(1) > body:nth-of-type(1) > div:nth-of-type(1)'
        scope, probe = self.scope(path, title_ancestor_parts=2)
        self.assertIsNone(scope)
        self.assertEqual(probe.read, ['.tn-page-vessel', d.TITLE])

    def test_ancestor_attempts_are_bounded(self):
        path = 'body > ' + ' > '.join('div:nth-of-type(%d)' % n for n in range(1, 30))
        scope, probe = self.scope(path, title_ancestor_parts=None)
        self.assertIsNone(scope)
        self.assertEqual(len(probe.read), 2 + d.SCOPE_ESCALATIONS)

    def test_never_returns_the_whole_page(self):
        """`body` is the read that overflowed, so it must never come back as a narrower scope."""
        scope, _ = self.scope('body > .tn-page-vessel', title_ancestor_parts=1)
        self.assertIsNone(scope)

    def test_truncated_ancestor_stops_without_reading_a_larger_scope(self):
        probe = self.Probe('body > a > b > c > section:nth-of-type(1)',
                           title_ancestor_parts=4, truncated=('body > a > b > c',))
        with self.assertRaises(Stop) as caught:
            d.body_scope(probe, '123')
        self.assertEqual(caught.exception.code, 'truncated_page')
        self.assertEqual(probe.read, ['.tn-page-vessel', d.TITLE, 'body > a > b > c'])

    def test_a_scope_without_the_vessel_is_not_accepted(self):
        scope, _ = self.scope('body > a > b > .tn-page-vessel', title_ancestor_parts=1)
        self.assertIsNone(scope)

    def test_the_whole_page_is_unreachable_as_a_scope(self):
        """The climb's floor is `body > <one child>`; `body` itself can never be returned.

        `body` is the read that overflowed, so returning it would look like a fix while changing
        nothing. This is an invariant of the loop bound (it stops at index 2), not a branch: if
        someone widened the bound to include `body`, this case fails.
        """
        # Title only present at the body level, so every narrower ancestor is rejected for missing
        # it and the climb runs to its floor.
        scope, probe = self.scope('body > a > b > c > .tn-page-vessel', title_ancestor_parts=1)
        self.assertIsNone(scope)
        self.assertEqual(probe.read[0], '.tn-page-vessel')
        self.assertIn('body > a', probe.read, 'the climb must reach its floor')
        self.assertNotIn('body', probe.read,
                         'the loop bound must keep `body` out of the candidate set entirely')

    def test_no_vessel_means_no_scope(self):
        class Empty:
            def snapshot(self, frame=None, selector='body'):
                return {'nodes': []}
        self.assertIsNone(d.body_scope(Empty(), '730830179'))

    def test_a_scope_that_never_gains_the_title_falls_back(self):
        scope, _ = self.scope('body > a > b > .tn-page-vessel', title_ancestor_parts=None)
        self.assertIsNone(scope)


class SnapshotTruncationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        for module in (check_runtime, d):
            patcher = patch.object(module, 'ROOT', self.root)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.folder = self.root / 'local' / 'evidence'
        self.folder.mkdir(parents=True, exist_ok=True)

    def browser(self, **payloads):
        return FakeBrowser(payloads)

    def test_truncated_nodes_are_refused(self):
        """The exact 18:10 failure: nodes_truncated must stop the read, not pass silently."""
        browser = self.browser(body=snapshot(nodes=[{'tag': 'div'}] * 3000, nodes_truncated=True))
        with self.assertRaises(Stop) as caught:
            browser.snapshot()
        self.assertEqual(caught.exception.code, 'truncated_page')

    def test_truncated_text_is_refused(self):
        browser = self.browser(body=snapshot(text_truncated=True))
        with self.assertRaises(Stop) as caught:
            browser.snapshot()
        self.assertEqual(caught.exception.code, 'truncated_page')

    def test_a_complete_snapshot_passes_through_unchanged(self):
        payload = snapshot(nodes=[{'tag': 'div', 'selector': 'body > div'}])
        browser = self.browser(body=payload)
        self.assertEqual(browser.snapshot(), payload)
        self.assertEqual(browser.selectors, ['body'], 'the default read must stay whole-page')

    def test_the_read_can_be_scoped_to_a_selector(self):
        """Scoping is the repo-side half of the fix: same bridge, narrower root.

        The scoped artifact is still subject to the same truncation guard -- scoping must not
        become a way to smuggle a partial read past the check.
        """
        browser = self.browser(**{
            'body': snapshot(nodes=[{'tag': 'div'}] * 3000, nodes_truncated=True),
            '.tn-page-vessel': snapshot(selector='.tn-page-vessel', nodes=[{'tag': 'p'}] * 40),
        })
        scoped = browser.snapshot(selector='.tn-page-vessel')
        self.assertEqual(scoped['selector'], '.tn-page-vessel')
        self.assertFalse(scoped['nodes_truncated'])
        self.assertEqual(browser.selectors, ['.tn-page-vessel'],
                         'a scoped read must not first fetch the whole page')

    def test_a_scoped_read_is_still_refused_when_it_is_truncated(self):
        browser = self.browser(**{
            '.tn-page-vessel': snapshot(selector='.tn-page-vessel', nodes_truncated=True)})
        with self.assertRaises(Stop) as caught:
            browser.snapshot(selector='.tn-page-vessel')
        self.assertEqual(caught.exception.code, 'truncated_page')


class BodyVesselScopeTests(unittest.TestCase):
    """`content()` must see the vessel, the title input and the cover metadata in one snapshot."""

    def nodes(self, with_title_inside):
        nodes = [
            {'tag': 'div', 'class': 'tn-page-vessel', 'selector': 'body > main > .tn-page-vessel',
             'rendered_text': '正文', 'rendered_text_truncated': False,
             'rect': {'x': 0, 'y': 0, 'width': 600, 'height': 800}, 'inline_style': ''},
            {'tag': 'p', 'selector': 'body > main > .tn-page-vessel > p',
             'paragraph_text': '第一段', 'paragraph_text_truncated': False, 'inline_style': ''},
            {'tag': 'div', 'class': 'cover', 'selector': 'body > main > .cover', 'inline_style': 'a'},
        ]
        title_selector = ('body > main > .tn-page-vessel > input' if with_title_inside
                          else 'body > header > input')
        nodes.append({'tag': 'input', 'placeholder': '请输入图文的标题', 'value': '示例标题',
                      'selector': title_selector})
        return nodes

    def test_content_accepts_a_scoped_root_that_still_holds_the_title(self):
        value = d.content(snapshot(nodes=self.nodes(with_title_inside=True),
                                   url='https://xiumi.us/studio/v5#/paper/for/730830179'))
        self.assertEqual(value['id'], '730830179')
        self.assertEqual(value['title'], '示例标题')
        self.assertEqual(value['text'], '正文')

    def test_content_refuses_a_snapshot_whose_title_input_is_absent(self):
        """Scoping too narrowly must fail loudly, not silently lose the title.

        The stop code is whatever `one()` raises for a missing singleton; the contract under test
        is that a scope without the title does NOT quietly return a value with no title.
        """
        with self.assertRaises(Stop) as caught:
            d.content(snapshot(nodes=[
                n for n in self.nodes(with_title_inside=True) if n['tag'] != 'input']))
        self.assertIsInstance(caught.exception.code, str)
        self.assertTrue(caught.exception.code)

    def test_content_still_refuses_a_truncated_snapshot(self):
        for flag in ('nodes_truncated', 'text_truncated'):
            with self.subTest(flag=flag):
                with self.assertRaises(Stop) as caught:
                    d.content(snapshot(nodes=self.nodes(with_title_inside=True), **{flag: True}))
                self.assertEqual(caught.exception.code, 'body_truncated')

    def test_content_refuses_a_truncated_vessel_text(self):
        nodes = self.nodes(with_title_inside=True)
        nodes[0]['rendered_text_truncated'] = True
        with self.assertRaises(Stop) as caught:
            d.content(snapshot(nodes=nodes))
        self.assertEqual(caught.exception.code, 'body_truncated')

    def test_content_refuses_a_truncated_paragraph(self):
        nodes = self.nodes(with_title_inside=True)
        nodes[1]['paragraph_text_truncated'] = True
        with self.assertRaises(Stop) as caught:
            d.content(snapshot(nodes=nodes))
        self.assertEqual(caught.exception.code, 'body_truncated')


class WaitReadOverrideTests(unittest.TestCase):
    """`wait` must poll through the caller's reader, not always the whole page."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        for module in (check_runtime, d):
            patcher = patch.object(module, 'ROOT', self.root)
            patcher.start()
            self.addCleanup(patcher.stop)
        (self.root / 'local' / 'evidence').mkdir(parents=True, exist_ok=True)

    def test_the_custom_reader_is_used_and_the_default_is_not(self):
        calls = []

        class Browser(check_runtime.Browser):
            def snapshot(self, frame=None, selector='body'):
                calls.append('default')
                return {'seen': False}

        reader_calls = []

        def reader():
            reader_calls.append('override')
            return {'seen': True}

        browser = Browser('local/evidence')
        result = browser.wait(lambda s: s['seen'], read=reader)
        self.assertEqual(result, {'seen': True})
        self.assertEqual(reader_calls, ['override'])
        self.assertEqual(calls, [], 'wait must not fall back to the whole-page read when given one')


class BodyCollectionScopeTests(unittest.TestCase):
    """The scope used for body collection must be narrow enough to fit, and wide enough to work."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_snapshot_body_reads_the_scope_and_not_the_whole_page(self):
        """A whole-page read is what exceeds the cap, so body reads must use the derived scope.

        The scope only makes sense if `content()` can still be satisfied through it, so the fake
        read returns a complete snapshot of the editor container: vessel, title input, a paragraph
        and a cover all inside `body > div:nth-of-type(1)`. If the code reverted to a `body` read,
        `ready()` would never satisfy and the reader would see additional calls for `body`.
        """
        requested = []
        editor = 'body > div:nth-of-type(1) > div:nth-of-type(2)'   # the vessel's PARENT: this is
        vessel_selector = editor + ' > section:nth-of-type(1)'            # exactly what body_scope derives

        def payload():
            return snapshot(selector=editor, nodes=[
                {'tag': 'div', 'class': 'tn-page-vessel other', 'selector': vessel_selector,
                 'rendered_text': '正文', 'rendered_text_truncated': False,
                 'rect': {'x': 0, 'y': 0, 'width': 600, 'height': 800}, 'inline_style': ''},
                {'tag': 'p', 'selector': vessel_selector + ' > p', 'paragraph_text': '第一段',
                 'paragraph_text_truncated': False, 'inline_style': ''},
                {'tag': 'input', 'placeholder': '请输入图文的标题', 'value': '示例标题',
                 'selector': editor + ' > input'},
                {'tag': 'div', 'class': 'cover', 'selector': editor + ' > .cover',
                 'inline_style': 'cover:1'},
            ])

        root = Path(self.tmp.name).resolve()
        for module in (check_runtime, d):
            patcher = patch.object(module, 'ROOT', root)
            patcher.start()
            self.addCleanup(patcher.stop)
        (root / 'local' / 'evidence').mkdir(parents=True, exist_ok=True)

        class Recorder(check_runtime.Browser):
            def __init__(self):
                super().__init__('local/evidence')
                self.scoped_reads = 0

            def snapshot(self, frame=None, selector='body'):
                requested.append(selector)
                if selector == '.tn-page-vessel':
                    return {'nodes': [{'class': 'tn-page-vessel other',
                                       'selector': vessel_selector}]}
                self.scoped_reads += 1
                if self.scoped_reads > 1:
                    # Stop once the scope has demonstrably been used for the polling reads.
                    raise Stop('stop_after_two_scoped_reads', 'enough')
                return payload()

        browser = Recorder()
        with self.assertRaises(Stop):
            d.snapshot_body(browser, '730830179')

        self.assertIn('.tn-page-vessel', requested, 'the vessel probe never ran')
        self.assertIn(editor, requested, 'body collection never read the derived scope')
        self.assertNotIn('body', requested,
                         'body collection requested the whole page: that is the 3000-element read '
                         'that stopped the 18:10 round')
        self.assertGreaterEqual(requested.count(editor), 1)


if __name__ == '__main__':
    unittest.main()

class BridgeCapContractTests(unittest.TestCase):
    """The Python side depends on exact JS caps, so pin them.

    `content()` decides whether an article's body is complete, and it does that from flags the
    injected script sets. The per-node `rendered_text` cap was raised 12000 -> 100000 because a
    large compilation's vessel text exceeded it and the slice keeps the BEGINNING, silently dropping
    the TRAILING credit block that normalized_credits() validates. Nothing else would notice that
    regression: the Python code is unchanged, so only these constants can be asserted.
    """

    SCRIPT = (pathlib.Path(__file__).resolve().parents[1] / 'scripts' / 'browser.py').read_text(
        encoding='utf-8')

    def test_per_node_text_cap_is_the_raised_value(self):
        self.assertIn("rendered_text:(e.innerText || '').slice(0, 100000)", self.SCRIPT)
        self.assertIn("rendered_text_truncated:(e.innerText || '').length > 100000", self.SCRIPT)

    def test_page_node_and_text_caps_are_unchanged(self):
        self.assertIn('nodes_truncated:elements.length > 3000', self.SCRIPT)
        self.assertIn("text:(root.innerText || '').slice(0, 100000)", self.SCRIPT)

    def test_paragraph_cap_stays_below_the_page_cap(self):
        # A per-paragraph cap must exist and be smaller than the whole-page text cap, otherwise a
        # single runaway paragraph could silently pass as complete.
        self.assertIn("paragraph_text_truncated:e.tagName === 'P' && (e.innerText || '').length > 10000",
                      self.SCRIPT)
