import unittest

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


if __name__ == "__main__":
    unittest.main()
