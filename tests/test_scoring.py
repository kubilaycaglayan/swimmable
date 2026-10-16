import unittest
from backend.scoring import POLICY, Policy, bands, score

class ScoringTests(unittest.TestCase):
    def test_boundaries(self):
        self.assertEqual(bands(.2999, 19.999, 28.999), {"wave":"comfortable","sea":"cold","air":"chilly"})
        self.assertEqual(bands(.3, 20, 29), {"wave":"fun","sea":"swimmable","air":"comfortable"})
        self.assertEqual(bands(.6, 24, 32), {"wave":"dangerous","sea":"swimmable","air":"comfortable"})
        self.assertEqual(bands(1, 24.001, 32.001), {"wave":"dangerous","sea":"comfortable","air":"hot"})

    def test_numeric_policy(self):
        self.assertEqual(POLICY.version, "1")
        self.assertEqual(score(0, 20, 29), 85)
        self.assertEqual(score(.3, 20, 29), 74)
        self.assertEqual(score(.6, 40, 32), 20)
        self.assertLessEqual(score(1, 40, 32), 20)
        self.assertRaises(ValueError, score, float('nan'), 20, 29)
        self.assertRaises(ValueError, Policy, wave_weight=.7)

if __name__ == "__main__": unittest.main()
