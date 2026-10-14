import unittest

from textutils import slugify, truncate, word_count


class TestTextutils(unittest.TestCase):
    def test_slugify_simple(self):
        self.assertEqual(slugify("Hello"), "hello")

    def test_word_count_simple(self):
        self.assertEqual(word_count("a b c"), 3)

    def test_truncate(self):
        self.assertEqual(truncate("abcdef", 4), "abc…")


if __name__ == "__main__":
    unittest.main()
