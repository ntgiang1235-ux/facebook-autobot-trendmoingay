import unittest
from unittest.mock import patch

import runner


class RecipeHelperTests(unittest.TestCase):
    def test_relevance_score_rewards_matching_food_terms(self):
        query = "chicken tikka masala indian curry"
        matching = "Chicken tikka masala curry served with rice"
        unrelated = "Fresh green salad on a wooden table"

        self.assertGreater(
            runner.recipe_image_relevance_score(query, matching),
            runner.recipe_image_relevance_score(query, unrelated),
        )
        self.assertGreaterEqual(runner.recipe_image_relevance_score(query, matching), 2)

    def test_recipe_fallback_queries_are_global_not_vietnam_only(self):
        queries = runner.build_recipe_fallback_queries("Spaghetti Carbonara")

        self.assertIn("Spaghetti Carbonara", queries[0])
        self.assertTrue(any("food" in q.lower() for q in queries))
        self.assertFalse(all("vietnam" in q.lower() for q in queries))

    def test_find_image_returns_none_when_pexels_has_no_image(self):
        sentinel = {"url": "https://unlicensed.example/image.jpg", "source": "bing"}
        with patch.object(runner, "search_pexels_image", return_value=None), patch.object(
            runner, "search_bing_image", return_value=sentinel
        ) as bing:
            result = runner.find_image("rare dish")

        self.assertIsNone(result)
        bing.assert_not_called()


if __name__ == "__main__":
    unittest.main()
