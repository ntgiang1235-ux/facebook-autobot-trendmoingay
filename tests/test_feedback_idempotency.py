import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from app.strategy_models import AdaptiveConfig


class FeedbackIdempotencyTests(unittest.TestCase):
    def test_same_vietnam_day_reuses_version_and_repairs_only_current_pointer(self):
        from app import feedback_loop

        def execute(query, params=()):
            del params
            if "FROM strategy_versions" in query and "created_at" in query:
                return [(7, "2026-09-01T00:30:00+00:00")]
            if "MAX(version_id)" in query:
                return [(7,)]
            raise AssertionError(f"unexpected SQL: {query}")

        config = AdaptiveConfig(
            current_strategy_version=None,
            last_good_strategy_version=5,
        )
        with patch.object(feedback_loop, "load_config", return_value=config), patch.object(
            feedback_loop, "save_config"
        ) as save_config, patch.object(
            feedback_loop, "load_learning_observations", return_value=[]
        ) as load_observations, patch.object(
            feedback_loop, "load_stats"
        ) as load_stats:
            result = feedback_loop.refresh_strategy(
                execute,
                now=datetime(2026, 9, 1, 2, 30, tzinfo=timezone.utc),
            )

        self.assertEqual(result.version_id, 7)
        self.assertEqual(result.updated_stat_count, 0)
        load_stats.assert_not_called()
        load_observations.assert_not_called()
        repaired = save_config.call_args.args[1]
        self.assertEqual(repaired.current_strategy_version, 7)
        self.assertEqual(repaired.last_good_strategy_version, 5)

    def test_new_vietnam_day_creates_next_version_without_promoting_last_good(self):
        from app import feedback_loop

        def execute(query, params=()):
            del params
            if "FROM strategy_versions" in query and "created_at" in query:
                return [(7, "2026-08-31T16:30:00+00:00")]
            if "MAX(version_id)" in query:
                return [(7,)]
            if "FROM style_registry" in query:
                return []
            raise AssertionError(f"unexpected SQL: {query}")

        config = AdaptiveConfig(
            current_strategy_version=7,
            last_good_strategy_version=5,
        )
        with patch.object(feedback_loop, "load_config", return_value=config), patch.object(
            feedback_loop, "load_learning_observations", return_value=[]
        ), patch.object(
            feedback_loop, "load_stats", return_value=[]
        ), patch.object(
            feedback_loop, "upsert_stat"
        ), patch.object(
            feedback_loop, "save_strategy_version"
        ) as save_version, patch.object(
            feedback_loop, "save_config"
        ) as save_config:
            result = feedback_loop.refresh_strategy(
                execute,
                now=datetime(2026, 8, 31, 17, 30, tzinfo=timezone.utc),
            )

        self.assertEqual(result.version_id, 8)
        save_version.assert_called_once()
        snapshot = save_version.call_args.args[1]
        self.assertEqual(snapshot.version_id, 8)
        self.assertFalse(snapshot.is_last_good)
        self.assertEqual(snapshot.config.current_strategy_version, 8)
        self.assertEqual(snapshot.config.last_good_strategy_version, 5)
        persisted = save_config.call_args.args[1]
        self.assertEqual(persisted.current_strategy_version, 8)
        self.assertEqual(persisted.last_good_strategy_version, 5)


if __name__ == "__main__":
    unittest.main()
