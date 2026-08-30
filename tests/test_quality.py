import unittest


class QualityScoringTests(unittest.TestCase):
    def test_weighted_quality_score(self):
        from app.quality import QualityRubric, combine_quality_score

        rubric = QualityRubric(
            novelty=80,
            hook=70,
            usefulness=90,
            readability=80,
            tone=80,
            cta=70,
        )

        self.assertEqual(combine_quality_score(rubric, []), 79.0)

    def test_thresholds(self):
        from app.quality import decision_for_score

        self.assertEqual(decision_for_score(75).action, "publish")
        self.assertEqual(decision_for_score(70).action, "rewrite")
        self.assertEqual(decision_for_score(64.9).action, "reject")

    def test_components_and_final_score_are_clamped(self):
        from app.quality import QualityRubric, combine_quality_score

        rubric = QualityRubric(200, -20, 100, 100, 100, 100)
        score = combine_quality_score(rubric, [])

        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 100.0)


if __name__ == "__main__":
    unittest.main()
