import sqlite3
import unittest


class CreativeStrategyTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(
            """
            CREATE TABLE style_registry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dimension TEXT NOT NULL,
                value TEXT NOT NULL,
                parent_value TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                promoted_at TEXT,
                retired_at TEXT,
                UNIQUE(dimension, value)
            );
            CREATE TABLE strategy_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dimension TEXT NOT NULL,
                value TEXT NOT NULL,
                sample_count INTEGER NOT NULL DEFAULT 0,
                weighted_score_14d REAL NOT NULL DEFAULT 50.0,
                recent_score_7d REAL NOT NULL DEFAULT 50.0,
                success_rate REAL NOT NULL DEFAULT 0.0,
                current_weight REAL NOT NULL DEFAULT 1.0,
                last_used_at TEXT,
                status TEXT NOT NULL DEFAULT 'insufficient_data',
                cooldown_until TEXT,
                retest_after TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE(dimension, value)
            );
            CREATE TABLE adaptive_config (
                id INTEGER PRIMARY KEY,
                adaptive_enabled INTEGER NOT NULL DEFAULT 1,
                auto_schedule_enabled INTEGER NOT NULL DEFAULT 1,
                auto_suspend_enabled INTEGER NOT NULL DEFAULT 1,
                exploration_rate REAL NOT NULL DEFAULT 0.20,
                baseline_daily_volume INTEGER NOT NULL DEFAULT 12,
                current_strategy_version INTEGER,
                last_good_strategy_version INTEGER,
                CHECK(id = 1)
            );
            INSERT INTO adaptive_config (
                id, adaptive_enabled, auto_schedule_enabled, auto_suspend_enabled,
                exploration_rate, baseline_daily_volume,
                current_strategy_version, last_good_strategy_version
            ) VALUES (1, 1, 1, 1, 0.20, 12, 4, 4);
            """
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def execute(self, query, params=()):
        cursor = self.conn.execute(query, params)
        rows = cursor.fetchall() if cursor.description is not None else []
        self.conn.commit()
        return rows

    def test_missing_learning_data_seeds_registry_and_returns_registered_baseline_profile(self):
        from app.creative_strategy import select_creative_profile
        from app.style_registry import SEED_STYLES

        profile = select_creative_profile(
            self.execute,
            run_key="run-1",
            category="post",
            strategy_version=4,
        )

        self.assertIn(profile.hook_type, SEED_STYLES["hook"])
        self.assertIn(profile.style_type, SEED_STYLES["tone"])
        self.assertIn(profile.cta_type, SEED_STYLES["cta"])
        [count] = self.execute("SELECT COUNT(*) FROM style_registry")
        self.assertEqual(count[0], sum(len(values) for values in SEED_STYLES.values()))

    def test_same_dispatch_identity_is_deterministic_across_retry(self):
        from app.creative_strategy import select_creative_profile

        first = select_creative_profile(
            self.execute,
            run_key="777-1-dispatch",
            category="finance",
            strategy_version=12,
        )
        second = select_creative_profile(
            self.execute,
            run_key="777-1-dispatch",
            category="finance",
            strategy_version=12,
        )

        self.assertEqual(first, second)

    def test_retired_registry_value_is_never_selected_even_if_stat_weight_is_huge(self):
        from app.creative_strategy import select_creative_profile

        self.conn.execute(
            """
            INSERT INTO style_registry (
                dimension, value, parent_value, status, created_at,
                promoted_at, retired_at
            ) VALUES ('hook', 'retired_hook', NULL, 'retired', '2026-08-01T00:00:00+00:00', NULL, '2026-08-10T00:00:00+00:00')
            """
        )
        self.conn.execute(
            """
            INSERT INTO strategy_stats (
                dimension, value, sample_count, weighted_score_14d,
                recent_score_7d, success_rate, current_weight,
                last_used_at, status, cooldown_until, retest_after, updated_at
            ) VALUES (
                'hook_type', 'retired_hook', 50, 100, 100, 1, 1000,
                NULL, 'active', NULL, NULL, '2026-08-30T00:00:00+00:00'
            )
            """
        )
        self.conn.execute("UPDATE adaptive_config SET exploration_rate = 0 WHERE id = 1")
        self.conn.commit()

        profile = select_creative_profile(
            self.execute,
            run_key="run-retired",
            category="post",
            strategy_version=4,
        )

        self.assertNotEqual(profile.hook_type, "retired_hook")

    def test_exploit_uses_eligible_learned_value_when_available(self):
        from app.creative_strategy import select_creative_profile

        self.conn.execute(
            """
            INSERT INTO strategy_stats (
                dimension, value, sample_count, weighted_score_14d,
                recent_score_7d, success_rate, current_weight,
                last_used_at, status, cooldown_until, retest_after, updated_at
            ) VALUES (
                'hook_type', 'question', 8, 82, 85, 0.75, 1,
                NULL, 'active', NULL, NULL, '2026-08-30T00:00:00+00:00'
            )
            """
        )
        self.conn.execute("UPDATE adaptive_config SET exploration_rate = 0 WHERE id = 1")
        self.conn.commit()

        profile = select_creative_profile(
            self.execute,
            run_key="run-exploit",
            category="post",
            strategy_version=4,
        )

        self.assertEqual(profile.hook_type, "question")

    def test_prompt_suffix_uses_fixed_safe_mapping_and_never_requires_invented_fact(self):
        from app.creative_strategy import CreativeProfile, creative_prompt_suffix

        suffix = creative_prompt_suffix(
            CreativeProfile(
                hook_type="surprising_fact",
                style_type="explanatory",
                cta_type="opinion_question",
            )
        )

        self.assertIn("không bịa", suffix.lower())
        self.assertIn("dữ kiện", suffix.lower())
        self.assertIn("câu hỏi", suffix.lower())
        self.assertNotIn("surprising_fact", suffix)


if __name__ == "__main__":
    unittest.main()
