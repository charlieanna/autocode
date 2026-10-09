import unittest

from learning.flow import Session


class JourneyTests(unittest.TestCase):
    def test_named_journeys(self):
        assisted, independent = Session("assisted"), Session("independent")
        assisted.read()
        assisted.hint()
        assisted.answer("5")
        independent.read()
        independent.answer("5")
        self.assertEqual(("addition", "subtraction"), (assisted.next(), independent.next()))
