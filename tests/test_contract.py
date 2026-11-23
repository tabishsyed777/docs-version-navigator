import unittest
from navigator import clean, chunks, search_terms

class TextContract(unittest.TestCase):
    def test_clean(self):
        text = "Do not use 10 ms.\r\n\r\n`x = 1` 😀 שלום"
        self.assertEqual(clean(clean(text)), clean(text))
        self.assertIn("not use 10 ms.", clean(text))
        self.assertIn("`x = 1` 😀 שלום", clean(text))

    def test_chunks(self):
        text = "First paragraph.\n\n```\nx = 1\n\nprint(x)\n```"
        units = chunks(text, 1)
        self.assertEqual("".join(x[2] for x in units), text)
        for start, end, value in units:
            self.assertEqual(value, text[start:end])

    def test_terms(self):
        self.assertIn("timeout", search_terms("What is the timeout?"))
