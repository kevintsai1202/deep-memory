# write_cold.py 精煉提示測試：提示必須依「待精煉（未 reviewed）筆數」判斷，而不是冷庫總筆數。
# 背景：冷庫累積到數千筆且絕大多數已精煉後，舊邏輯（總數 ≥ 20）每寫一筆都會提示精煉，失去意義。
import json
import tempfile
import unittest
from pathlib import Path

import write_cold


def _write_lines(lines):
    """建立臨時 raw.jsonl 並回傳路徑。"""
    d = Path(tempfile.mkdtemp())
    p = d / "raw.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


class TestCountNotes(unittest.TestCase):
    def test_counts_total_and_pending(self):
        p = _write_lines([
            json.dumps({"topic": "a", "quality": "reviewed"}),
            json.dumps({"topic": "b", "quality": "raw"}),
            json.dumps({"topic": "c"}),  # 沒有 quality 欄位視為待精煉
            "",
            "{壞掉的行",  # 無法解析的行計入總數但不算待精煉
        ])
        total, pending = write_cold.count_notes(p)
        self.assertEqual(total, 4)
        self.assertEqual(pending, 2)


class TestRefineHint(unittest.TestCase):
    def test_no_hint_below_pending_threshold_even_when_total_is_huge(self):
        self.assertEqual(write_cold.refine_hint(total=3310, pending=2), [])

    def test_hint_when_pending_reaches_threshold(self):
        lines = write_cold.refine_hint(total=500, pending=30)
        self.assertTrue(lines)
        text = "\n".join(lines)
        self.assertIn("30", text)   # 待精煉筆數
        self.assertIn("500", text)  # 總筆數也要顯示，避免誤會
        self.assertIn("幫我精煉冷庫", text)

    def test_threshold_is_30(self):
        self.assertEqual(write_cold.REFINE_THRESHOLD, 30)
        self.assertEqual(write_cold.refine_hint(total=100, pending=29), [])


if __name__ == "__main__":
    unittest.main()
