import unittest


class StyleRegistryTests(unittest.TestCase):
    def test_seed_styles_cover_approved_hooks_tones_and_ctas(self):
        from app.style_registry import ensure_seed_styles

        calls = []

        def execute(query, params=()):
            calls.append((query, params))
            return []

        ensure_seed_styles(execute)

        seeded = {(params[0], params[1], params[3]) for query, params in calls}
        for value in (
            "question",
            "number",
            "surprising_fact",
            "direct_statement",
            "contrast",
            "curiosity",
        ):
            self.assertIn(("hook", value, "baseline"), seeded)
        for value in (
            "concise_news",
            "conversational",
            "witty",
            "explanatory",
            "reflective",
        ):
            self.assertIn(("tone", value, "baseline"), seeded)
        for value in (
            "opinion_question",
            "choose_side",
            "experience_share",
            "save_for_later",
            "no_cta",
        ):
            self.assertIn(("cta", value, "baseline"), seeded)
        self.assertTrue(all("INSERT OR IGNORE" in query for query, _ in calls))

    def test_list_active_styles_maps_rows_and_excludes_retired(self):
        from app.style_registry import list_active_styles

        calls = []

        def execute(query, params=()):
            calls.append((query, params))
            return [
                (
                    7,
                    "hook",
                    "number",
                    None,
                    "active",
                    "2026-08-30T00:00:00+00:00",
                    "2026-08-30T01:00:00+00:00",
                    None,
                )
            ]

        rows = list_active_styles(execute, "hook")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].id, 7)
        self.assertEqual(rows[0].value, "number")
        query, params = calls[0]
        self.assertIn("status != 'retired'", query)
        self.assertEqual(params, ("hook",))

    def test_register_experiment_preserves_parent_and_explore_status(self):
        from app.style_registry import register_experiment

        calls = []

        def execute(query, params=()):
            calls.append((query, params))
            return [(11,)]

        style_id = register_experiment(
            execute,
            "hook",
            "quick_contrast",
            "contrast",
            created_at="2026-08-30T02:00:00+00:00",
        )

        self.assertEqual(style_id, 11)
        query, params = calls[0]
        self.assertIn("INSERT OR IGNORE", query)
        self.assertEqual(params[:4], ("hook", "quick_contrast", "contrast", "explore"))

    def test_set_style_status_rejects_unknown_status_before_sql(self):
        from app.style_registry import set_style_status

        calls = []

        def execute(query, params=()):
            calls.append((query, params))
            return []

        with self.assertRaises(ValueError):
            set_style_status(execute, 5, "winner")

        self.assertEqual(calls, [])

    def test_set_style_status_updates_lifecycle_timestamps(self):
        from app.style_registry import set_style_status

        calls = []

        def execute(query, params=()):
            calls.append((query, params))
            return []

        set_style_status(
            execute,
            5,
            "active",
            changed_at="2026-08-30T03:00:00+00:00",
        )
        set_style_status(
            execute,
            6,
            "retired",
            changed_at="2026-08-30T04:00:00+00:00",
        )

        self.assertIn("promoted_at", calls[0][0])
        self.assertEqual(calls[0][1], ("active", "2026-08-30T03:00:00+00:00", 5))
        self.assertIn("retired_at", calls[1][0])
        self.assertEqual(calls[1][1], ("retired", "2026-08-30T04:00:00+00:00", 6))


if __name__ == "__main__":
    unittest.main()
