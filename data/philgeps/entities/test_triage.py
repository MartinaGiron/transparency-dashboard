"""
Tests for triage.py.

Usage (from data/philgeps/entities):
    python -m unittest test_triage
"""

import unittest

from triage import core, triage, year_handoff


def pair(name_a, name_b, shared=0, orgs_a=10, orgs_b=10, years_a="", years_b="", dpwh="none"):
    return {"name_a": name_a, "name_b": name_b, "shared_organizations": str(shared),
            "organizations_a": str(orgs_a), "organizations_b": str(orgs_b),
            "years_a": years_a, "years_b": years_b, "dpwh_evidence": dpwh}


class CoreTest(unittest.TestCase):
    def test_examples(self):
        self.assertEqual(core("2-R CONSTRUCTION & SUPPLY"), "2R")
        self.assertEqual(core("AL NIÑO CONSTRUCTION"), core("ALNINO CONSTRUCTION CORP."))
        self.assertEqual(core("GEORAVIN CONSTRUCTION &AMP; SUPPLIES"), "GEORAVIN")

    def test_punctuation_splits_words(self):
        self.assertEqual(core("PEDXING BUILDERS,INC"), "PEDXING")
        self.assertEqual(core("NINA'S CONSTRUCTION"), "NINAS")

    def test_misspelled_trade_words(self):
        self.assertEqual(core("OIC CONZTRUCT AND SUPPLY CORPORATION"), "OIC")
        self.assertEqual(core("ROIM BUILDERS AND CONSTRUCTION SUPPY"), "ROIM")
        self.assertEqual(core("ABC CONTRUCTION"), "ABC")

    def test_keeps_real_words_near_short_trade_words(self):
        self.assertEqual(core("K SERVICO TRADE INC"), "KSERVICOTRADE")
        self.assertEqual(core("K-SERVICE TRADE INC."), "KTRADE")

    def test_trailing_s(self):
        self.assertEqual(core("ACME SYSTEMS"), core("ACME SYSTEM"))
        self.assertEqual(core("ABS CONSTRUCTION"), "ABS")  # 3 letters: S kept

    def test_plus_sign(self):
        self.assertEqual(core("C+ CONSTRUCTION AND DEVELOPMENT CORPORATION"), "CPLU")


class YearHandoffTest(unittest.TestCase):
    def test_handoff(self):
        self.assertTrue(year_handoff("2008-2020", "2021-2025"))
        self.assertTrue(year_handoff("2021-2025", "2013-2020"))
        self.assertFalse(year_handoff("2008-2025", "2010-2020"))  # later range ends first
        self.assertFalse(year_handoff("2008-2012", "2020-2025"))  # gap over 2 years
        self.assertFalse(year_handoff("", "2020"))


class LabelTest(unittest.TestCase):
    def label(self, **kw):
        return triage(pair(**kw))["relation_label"]

    def test_dpwh_overrides(self):
        self.assertEqual(self.label(name_a="EGB CONSTRUCTION", name_b="EGB CONSTRUCTION CORP", dpwh="same_id"), "same_entity")
        self.assertEqual(self.label(name_a="YAKAL CONSTRUCTION", name_b="YAKAL CONSTRUCTION AND SUPPLY",
                                    shared=1, dpwh="different_id"), "related_likely")
        self.assertEqual(self.label(name_a="DOUBLE J CONSTRUCTION", name_b="DOUBLE Z CONSTRUCTION",
                                    dpwh="different_id"), "unrelated_likely")

    def test_same_core(self):
        self.assertEqual(self.label(name_a="TM ABAD CONSTRUCTION CORP.", name_b="TM ABAD CONSTRUCTION", shared=9),
                         "same_or_successor_likely")
        self.assertEqual(self.label(name_a="TM ABAD CONSTRUCTION CORP.", name_b="TM ABAD CONSTRUCTION",
                                    years_a="2008-2020", years_b="2021-2025"), "same_or_successor_likely")
        self.assertEqual(self.label(name_a="MG CONSTRUCTION", name_b="MG BUILDERS"), "check")

    def test_branch(self):
        self.assertEqual(self.label(name_a="ARMSCOR SHOOTING CENTER, INC.-MARIKINA BRANCH",
                                    name_b="ARMSCOR SHOOTING CENTER, INC.-MAKATI BRANCH", shared=3), "same_parent_unit")

    def test_heavy_overlap_different_names(self):
        self.assertEqual(self.label(name_a="HEALTHSOLUTIONS ENTERPRISES INC.", name_b="HEALTH OPTIONS ENTERPRISES",
                                    shared=11, orgs_b=14), "related_check")

    def test_unrelated(self):
        self.assertEqual(self.label(name_a="GOLDRIDGE CONSTRUCTION", name_b="GOLDROCK CONSTRUCTION"), "unrelated_likely")


class EvidenceTest(unittest.TestCase):
    def test_score_and_text(self):
        t = triage(pair("EGB CONSTRUCTION", "EGB CONSTRUCTION CORP", shared=15, orgs_a=34, orgs_b=47,
                        years_a="2008-2020", years_b="2020-2025", dpwh="same_id"))
        self.assertEqual(t["evidence_score"], 10)
        self.assertEqual(t["relation_evidence"], "same DPWH contractor ID; identical name core; "
                                                 "15 shared buyers (44% of smaller); name handoff 2008-2020 / 2020-2025")
        self.assertEqual(t["next_action"], "merge")

    def test_weak_buyer_overlap_text(self):
        t = triage(pair("ABC CONSTRUCTION", "ABC BUILDERS", shared=1, orgs_a=20, orgs_b=20))
        self.assertIn("1 shared buyer(s)", t["relation_evidence"])

    def test_foreign_form(self):
        t = triage(pair("KOLONWEL TRADING", "KOLONWEL TRADING CO. LTD.", shared=1, orgs_a=1, orgs_b=1))
        self.assertIn("foreign entity: not in SEC/DTI", t["relation_evidence"])


if __name__ == "__main__":
    unittest.main()
