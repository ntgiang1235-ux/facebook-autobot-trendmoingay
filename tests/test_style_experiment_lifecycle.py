import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.learning_repository import LearningObservation
from app.style_registry import StyleVariant


NOW = datetime(2026, 8, 31, 1, 0, tzinfo=timezone.utc)


def variant(identifier=9, dimension="tone", value="witty_short_punchline", parent="witty", status="explore"):
    return StyleVariant(
        identifier,
        dimension,
        value,
        parent,
        status,
        "2026-08-24T00:00:00+00:00",
        None,
        None,
    )


def observation(
    post_id,
    score,
    *,
    experiment_key=None,
    style_type="witty",
    hook_type="question",
    cta_type="choose_side",
    age_days=2,
    score_kind="final",
):
    return LearningObservation(
        facebook_post_id=post_id,
        category="fun",
        time_bucket="10:00",
        hook_type=hook_type,
        style_type=style_type,
        cta_type=cta_type,
        format_type="text",
        score=float(score),
        score_kind=score_kind,
        published_at=NOW - timedelta(days=age_days),
        style_experiment_key=experiment_key,
    )


def mature_group(prefix, score, count=5, **kwargs):
    return [observation(f"{prefix}-{index}", score, **kwargs) for index in range(count)]


class StyleExperimentLifecycleTests(unittest.TestCase):
    @patch("app.style_experiment_lifecycle.set_style_status")
    @patch("app.style_experiment_lifecycle.load_learning_observations")
    @patch("app.style_experiment_lifecycle.list_active_styles", return_value=[])
    def test_no_pending_experiment_is_noop(self, _list_styles, load_observations, set_status):
        from app.style_experiment_lifecycle import review_pending_experiment

        result = review_pending_experiment(lambda *_: [], now=NOW)

        self.assertEqual(result.status, "no_pending")
        load_observations.assert_not_called()
        set_status.assert_not_called()

    @patch("app.style_experiment_lifecycle.set_style_status")
    @patch("app.style_experiment_lifecycle.load_learning_observations")
    @patch("app.style_experiment_lifecycle.list_active_styles")
    def test_fewer_than_five_mature_experiment_posts_keeps_explore(
        self, list_styles, load_observations, set_status
    ):
        from app.style_experiment_lifecycle import review_pending_experiment

        list_styles.side_effect = lambda _execute, dimension: [variant()] if dimension == "tone" else []
        load_observations.return_value = (
            mature_group(
                "exp", 84, count=4,
                experiment_key="tone:witty_short_punchline",
                style_type="witty",
            )
            + mature_group("parent", 75, count=5, style_type="witty")
        )

        result = review_pending_experiment(lambda *_: [], now=NOW)

        self.assertEqual(result.status, "insufficient_experiment_data")
        self.assertEqual(result.experiment_mature_samples, 4)
        set_status.assert_not_called()

    @patch("app.style_experiment_lifecycle.set_style_status")
    @patch("app.style_experiment_lifecycle.load_learning_observations")
    @patch("app.style_experiment_lifecycle.list_active_styles")
    def test_insufficient_parent_control_keeps_explore(
        self, list_styles, load_observations, set_status
    ):
        from app.style_experiment_lifecycle import review_pending_experiment

        list_styles.side_effect = lambda _execute, dimension: [variant()] if dimension == "tone" else []
        load_observations.return_value = (
            mature_group(
                "exp", 84, count=5,
                experiment_key="tone:witty_short_punchline",
                style_type="witty",
            )
            + mature_group("parent", 75, count=4, style_type="witty")
        )

        result = review_pending_experiment(lambda *_: [], now=NOW)

        self.assertEqual(result.status, "insufficient_parent_data")
        self.assertEqual(result.parent_mature_samples, 4)
        set_status.assert_not_called()

    @patch("app.style_experiment_lifecycle.set_style_status")
    @patch("app.style_experiment_lifecycle.load_learning_observations")
    @patch("app.style_experiment_lifecycle.list_active_styles")
    def test_clear_winner_is_promoted(self, list_styles, load_observations, set_status):
        from app.style_experiment_lifecycle import review_pending_experiment

        list_styles.side_effect = lambda _execute, dimension: [variant()] if dimension == "tone" else []
        load_observations.return_value = (
            mature_group(
                "exp", 84, count=5,
                experiment_key="tone:witty_short_punchline",
                style_type="witty",
            )
            + mature_group("parent", 75, count=5, style_type="witty")
        )

        result = review_pending_experiment(lambda *_: [], now=NOW)

        self.assertEqual(result.status, "promoted")
        self.assertGreater(result.experiment_score_14d, result.parent_score_14d)
        set_status.assert_called_once_with(
            unittest.mock.ANY,
            9,
            "active",
            changed_at=NOW.isoformat(),
        )

    @patch("app.style_experiment_lifecycle.set_style_status")
    @patch("app.style_experiment_lifecycle.load_learning_observations")
    @patch("app.style_experiment_lifecycle.list_active_styles")
    def test_child_experiment_compares_against_promoted_custom_parent_exposures(
        self, list_styles, load_observations, set_status
    ):
        from app.style_experiment_lifecycle import review_pending_experiment

        parent = variant(
            identifier=8,
            value="witty_short_punchline",
            parent="witty",
            status="active",
        )
        child = variant(
            identifier=10,
            value="witty_micro_story",
            parent="witty_short_punchline",
            status="explore",
        )
        list_styles.side_effect = lambda _execute, dimension: [parent, child] if dimension == "tone" else []
        load_observations.return_value = (
            mature_group(
                "child", 86, count=5,
                experiment_key="tone:witty_micro_story",
                style_type="witty",
            )
            + mature_group(
                "custom-parent", 76, count=5,
                experiment_key="tone:witty_short_punchline",
                style_type="witty",
            )
        )

        result = review_pending_experiment(lambda *_: [], now=NOW)

        self.assertEqual(result.status, "promoted")
        self.assertEqual(result.parent_mature_samples, 5)
        set_status.assert_called_once_with(
            unittest.mock.ANY,
            10,
            "active",
            changed_at=NOW.isoformat(),
        )

    @patch("app.style_experiment_lifecycle.set_style_status")
    @patch("app.style_experiment_lifecycle.load_learning_observations")
    @patch("app.style_experiment_lifecycle.list_active_styles")
    def test_clearly_weak_variant_is_retired(self, list_styles, load_observations, set_status):
        from app.style_experiment_lifecycle import review_pending_experiment

        list_styles.side_effect = lambda _execute, dimension: [variant()] if dimension == "tone" else []
        load_observations.return_value = (
            mature_group(
                "exp", 55, count=5,
                experiment_key="tone:witty_short_punchline",
                style_type="witty",
            )
            + mature_group("parent", 80, count=5, style_type="witty")
        )

        result = review_pending_experiment(lambda *_: [], now=NOW)

        self.assertEqual(result.status, "retired")
        set_status.assert_called_once_with(
            unittest.mock.ANY,
            9,
            "retired",
            changed_at=NOW.isoformat(),
        )

    @patch("app.style_experiment_lifecycle.set_style_status")
    @patch("app.style_experiment_lifecycle.load_learning_observations")
    @patch("app.style_experiment_lifecycle.list_active_styles")
    def test_mature_but_inconclusive_variant_remains_explore(
        self, list_styles, load_observations, set_status
    ):
        from app.style_experiment_lifecycle import review_pending_experiment

        list_styles.side_effect = lambda _execute, dimension: [variant()] if dimension == "tone" else []
        load_observations.return_value = (
            mature_group(
                "exp", 78, count=5,
                experiment_key="tone:witty_short_punchline",
                style_type="witty",
            )
            + mature_group("parent", 80, count=5, style_type="witty")
        )

        result = review_pending_experiment(lambda *_: [], now=NOW)

        self.assertEqual(result.status, "kept_explore")
        set_status.assert_not_called()


if __name__ == "__main__":
    unittest.main()
