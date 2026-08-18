import datetime
import unittest

from pipeline.price_ingest import build_tpex_snapshot


class BuildTpexSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.companies = [{"c": "3265", "n": "台星科"}]
        self.old = {
            "date": "20260813",
            "companies": self.companies,
            "q": {"3265": {"pe": 25.39, "pb": 3.9, "yield": 2.27, "close": 181.0}},
        }

    def test_new_close_is_published_when_ratio_source_fails(self):
        snap = build_tpex_snapshot(
            self.companies,
            {"3265": 171.0},
            datetime.date(2026, 8, 18),
            None,
            self.old,
        )
        self.assertEqual(snap["date"], "20260818")
        self.assertEqual(snap["ratioDate"], "20260813")
        self.assertEqual(snap["q"]["3265"], {
            "pe": 25.39,
            "pb": 3.9,
            "yield": 2.27,
            "close": 171.0,
        })

    def test_fresh_ratios_replace_old_values(self):
        snap = build_tpex_snapshot(
            self.companies,
            {"3265": 171.0},
            datetime.date(2026, 8, 18),
            {"3265": (24.0, 3.5, 2.4)},
            self.old,
        )
        self.assertEqual(snap["ratioDate"], "20260818")
        self.assertEqual(snap["q"]["3265"], {
            "pe": 24.0,
            "pb": 3.5,
            "yield": 2.4,
            "close": 171.0,
        })


if __name__ == "__main__":
    unittest.main()
