"""
Tests for normalize.py.

Usage (from data/philgeps/entities):
    python -m unittest test_normalize
"""

import unittest

from normalize import decode_entities, is_joint_venture, name_keys


class DecodeEntitiesTest(unittest.TestCase):
    def test_uppercase_entities(self):
        self.assertEqual(decode_entities("GEORAVIN CONSTRUCTION &AMP; SUPPLIES"), "GEORAVIN CONSTRUCTION & SUPPLIES")
        self.assertEqual(decode_entities("A&NBSP;B"), "A\xa0B")
        self.assertEqual(decode_entities("A &GT; B"), "A > B")

    def test_plain_ampersand_untouched(self):
        self.assertEqual(decode_entities("A & B CO"), "A & B CO")


class CleanKeyTest(unittest.TestCase):
    """Same clean_key = merged automatically, so these must be conservative."""

    def same(self, a, b):
        self.assertEqual(name_keys(a)["clean_key"], name_keys(b)["clean_key"], (a, b))

    def different(self, a, b):
        self.assertNotEqual(name_keys(a)["clean_key"], name_keys(b)["clean_key"], (a, b))

    def test_case_punctuation_spacing(self):
        self.same("ABC CONSTRUCTION INC.", "Abc Construction, Inc")
        self.same("ABC  CONSTRUCTION   INC", " abc construction inc ")
        self.same("GOLDMAN'S SUPPLY", "GOLDMANS SUPPLY")

    def test_entities_and_ampersand(self):
        self.same("GEORAVIN CONSTRUCTION &AMP; SUPPLIES", "GEORAVIN CONSTRUCTION & SUPPLIES")
        self.same("A & B BUILDERS", "A AND B BUILDERS")

    def test_abbreviations(self):
        self.same("ROCKSTEEL CONSTRUCTION AND DEV'T CORPORATION", "ROCKSTEEL CONSTRUCTION AND DEVELOPMENT CORP.")
        self.same("XYZ GEN. MDSE.", "XYZ GENERAL MERCHANDISE")
        self.same("FORD TRACTOR (PHILS.) INC.", "FORD TRACTOR PHILIPPINES INCORPORATED")

    def test_accents(self):
        self.same("AL NIÑO CONSTRUCTION", "AL NINO CONSTRUCTION")
        self.different("AL NIÑO CONSTRUCTION", "AL NI O CONSTRUCTION")

    def test_initials(self):
        self.same("J.T. GAMOLO CONSTRUCTION", "JT GAMOLO CONSTRUCTION")
        self.same("A.P.B. CONSTRUCTION", "APB CONSTRUCTION")
        self.same("R. D. POLICARPIO & CO., INC.", "R.D. POLICARPIO & CO., INC.")
        self.different("C & F CONSTRUCTION", "CF CONSTRUCTION")

    def test_legal_form_is_not_merged_automatically(self):
        self.different("EGB CONSTRUCTION", "EGB CONSTRUCTION CORPORATION")

    def test_numbers_matter(self):
        self.different("729 CONSTRUCTION, INC.", "722 CONSTRUCTION, INC.")
        self.different("JMC BUILDERS 1", "JMC BUILDERS 2")

    def test_person_names_not_expanded(self):
        self.different("PHIL SANTOS TRADING", "PHILIPPINES SANTOS TRADING")


class CoreAndLooseKeyTest(unittest.TestCase):
    def test_core_strips_legal_forms(self):
        self.assertEqual(name_keys("EGB CONSTRUCTION CORPORATION")["core_key"], "EGB CONSTRUCTION")
        self.assertEqual(name_keys("EGB CONSTRUCTION")["core_key"], "EGB CONSTRUCTION")
        self.assertEqual(name_keys("SUNWEST, INC.")["core_key"], "SUNWEST")
        self.assertEqual(name_keys("ABC CO. INC.")["core_key"], "ABC")

    def test_core_keeps_inner_words(self):
        self.assertEqual(name_keys("ABC CO DEVELOPMENT")["core_key"], "ABC CO DEVELOPMENT")

    def test_never_strips_to_empty(self):
        self.assertEqual(name_keys("INC")["core_key"], "INC")
        self.assertEqual(name_keys("TRADING")["loose_key"], "TRADING")

    def test_loose_strips_business_types(self):
        self.assertEqual(name_keys("RELY CONSTRUCTION AND SUPPLY")["loose_key"], "RELY CONSTRUCTION")
        self.assertEqual(name_keys("XYZ GENERAL MERCHANDISE")["loose_key"], "XYZ")
        self.assertEqual(name_keys("XYZ TRADING")["loose_key"], "XYZ")


class AnnotationTest(unittest.TestCase):
    def test_formerly_in_parentheses(self):
        keys = name_keys("729 CONSTRUCTION, INC. (FORMERLY: 722 CONSTRUCTION, INC.)")
        self.assertEqual(keys["clean_key"], "729 CONSTRUCTION INC")
        self.assertEqual(keys["notes"], ["(FORMERLY: 722 CONSTRUCTION, INC.)"])

    def test_formerly_without_parentheses(self):
        self.assertEqual(name_keys("LSERV CORPORATION FORMERLY: LBP SERVICE CORPORATION")["clean_key"], "LSERV CORP")

    def test_doing_business_as(self):
        keys = name_keys("GOLDMAN'S SUPPLY CORPORATION DOING BUSINESS UNDER THE NAME AND STYLE OF GOLDMAN ENTERPRISE")
        self.assertEqual(keys["clean_key"], "GOLDMANS SUPPLY CORP")
        self.assertEqual(len(keys["notes"]), 1)


class JointVentureTest(unittest.TestCase):
    def test_detects_markers(self):
        for name in [
            "EGB CONSTRUCTION CORPORATION/DALCON CONSTRUCTION JV",
            "EGB CONSTRUCTION / DALCON CONSTRUCTION - JOIN VENTURE",
            "EGB CONSTRUCTION/DALCON CONSTRUCTION (JV)",
            "ABC AND XYZ BUILDERS JOINT VENTURE",
            "A/B CONSORTIUM",
            "N.B. AVILA CONSTRUCTION / SELPHAR CONSTRUCTION (J.V.)",
            "NEWBIG FOUR J CONSTRUCTION INC / JTC CRUZ CONSTRUCTION CO, INC. ( J. V )",
            "MEDILINES JV WITH R.A. TAGALA",
        ]:
            self.assertTrue(is_joint_venture(name), name)

    def test_ignores_false_positives(self):
        for name in [
            "LAYSON JV & CO., INC.",
            "BASICS JVTM GENERAL MERCHANDISE",
            "PRINCESS JV GENERAL MERCHANDISE",
            "CJF VISMIN 88 CORP. DOING BUSINESS UNDER THE NAME AND STYLE OF JVS AUDIO SYSTEM",
            "A & B CONSTRUCTION",
            "J.V. CRUZ TRADING",
            "J. V. SANTOS CONSTRUCTION",
        ]:
            self.assertFalse(is_joint_venture(name), name)


if __name__ == "__main__":
    unittest.main()
