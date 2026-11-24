import unittest
import navigator
class ReleaseContext(unittest.TestCase):
    def test_examples_do_not_override_scope(self):
        for sample in ['"v8.2"','`release 8.2`',"'version 8.2'",'example: v8.2','e.g. version 8.2','returns v8.2','equals release 8.2']:
            with self.subTest(sample=sample):
                navigator.strip_scope('timeout '+sample,['1.0'])
        with self.assertRaises(ValueError):navigator.strip_scope('timeout in release 8.2',['1.0'])
