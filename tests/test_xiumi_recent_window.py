import unittest
from datetime import date
from scripts.xiumi_scan import recent_cards
from scripts.check_runtime import Stop

class RecentWindowTests(unittest.TestCase):
    def test_fourteen_calendar_days_and_old_exact_title(self):
        cards=[{'id':str(i),'title':'same','display_date':d} for i,d in enumerate(['2026-10-07','2026-09-24','2026-09-23','2026-04-25'])]
        self.assertEqual([c['id'] for c in recent_cards(cards,date(2026,10,7))],['0','1'])
    def test_unknown_and_future_dates_fail_instead_of_missing_draft(self):
        for card in ({}, {'display_date':'invalid'}, {'display_date':'2026-10-08'}):
            with self.subTest(card=card), self.assertRaises(Stop):
                recent_cards([card],date(2026,10,7))
    def test_restore_only_entry_is_not_eligible(self):
        self.assertEqual(recent_cards([{'available':False}],date(2026,10,7)),[])
