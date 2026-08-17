#!/usr/bin/env python3
"""Redaction tests — real secrets go, ordinary prose stays.

    python3 tests/test_redact.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import redact  # noqa: E402


class RedactTest(unittest.TestCase):

    def assertRedacted(self, text):
        self.assertIn(redact.REDACTED, redact.clean(text), f"should have been redacted: {text}")

    def assertKept(self, text):
        self.assertEqual(redact.clean(text), text, f"should have been left alone: {text}")

    # -- still catches the real thing -------------------------------------

    def test_openai_style_key_is_redacted(self):
        self.assertRedacted("key: sk-abcdefghij0123456789ABCDEFghij")

    def test_anthropic_style_key_is_redacted(self):
        self.assertRedacted("key: sk-ant-api03-AAAABBBBCCCCDDDDEEEEFFFF")

    def test_a_key_is_redacted_after_punctuation(self):
        for prefix in ("=", ":", "(", "'", '"', ",", "\n", " ", "-"):
            with self.subTest(prefix=prefix):
                self.assertRedacted(f"token{prefix}sk-abcdefghij0123456789ABCDEFghij")

    # -- but not ordinary hyphenated prose --------------------------------

    def test_hyphenated_words_ending_in_sk_are_kept(self):
        # Each contains "sk-" followed by 20+ word characters.
        for phrase in (
            "ask-again-once-the-migration-lands",
            "task-queue-retry-backoff-limit",
            "risk-weighted-average-cost-basis",
            "disk-usage-alerting-threshold-v2",
            "desk-booking-service-integration",
        ):
            with self.subTest(phrase=phrase):
                self.assertKept(phrase)

    def test_a_memory_cross_reference_survives(self):
        self.assertKept("See [[risk-register-review-checklist]] for the escalation rule.")

    def test_a_filename_survives(self):
        self.assertKept("notes/disk-usage-alerting-threshold.md")

    # -- unrelated rules still work ---------------------------------------

    def test_private_tags_still_drop_content(self):
        self.assertNotIn("hunter2", redact.clean("pw <private>hunter2</private> ok"))

    def test_jwt_is_redacted(self):
        self.assertRedacted(
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U")


if __name__ == "__main__":
    unittest.main(verbosity=2)
