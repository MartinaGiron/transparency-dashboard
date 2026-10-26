"""
Tests for jv_members.py.

Usage (from data/philgeps/entities):
    python -m unittest test_jv_members
"""

import unittest

from jv_members import Contractors, best_split, dpwh_former, dpwh_name, pair_with_dpwh, strip_markers

KNOWN = Contractors([
    "EGB CONSTRUCTION CORPORATION", "DALCON CONSTRUCTION", "KHAN KON CHI CONSTRUCTION & DEVELOPMENT CORPORATION",
    "MRBII CONSTRUCTION CORPORATION", "NTT DATA PHILIPPINES, INC.", "MEDILINES DISTRIBUTORS INCORPORATED",
    "AUTRE PORTE TECHNIQUE GLOBAL INC.", "MULTI- SCAN CORPORATION", "OPUSLAND INC.", "A & B BUILDERS",
    "THREE W BUILDERS, INC.", "TAGUM BUILDERS CONTRACTORS CORPORATION",
])


def members(name):
    _, parts, matches = best_split(strip_markers(name), KNOWN)
    return [m or p for p, (m, _) in zip(parts, matches)]


class StripMarkersTest(unittest.TestCase):
    def test_markers_removed(self):
        self.assertEqual(strip_markers("EGB CONSTRUCTION/DALCON CONSTRUCTION (JV)"), "EGB CONSTRUCTION/DALCON CONSTRUCTION")
        self.assertEqual(strip_markers("A / B - JOIN VENTURE"), "A / B")
        self.assertEqual(strip_markers("JOINT VENTURE OF NTT DATA PHILIPPINES, INC. AND X"), "NTT DATA PHILIPPINES, INC. AND X")

    def test_mid_marker_becomes_separator(self):
        self.assertEqual(strip_markers("A CORPORATION JOINT VENTURE WITH B CORPORATION"), "A CORPORATION / B CORPORATION")
        self.assertEqual(strip_markers("MEDILINES JV WITH R.A. TAGALA"), "MEDILINES / R.A. TAGALA")


class SplitTest(unittest.TestCase):
    def test_slash(self):
        self.assertEqual(members("EGB CONSTRUCTION CORPORATION/DALCON CONSTRUCTION JV"),
                         ["EGB CONSTRUCTION CORPORATION", "DALCON CONSTRUCTION"])

    def test_and_inside_a_member_name(self):
        self.assertEqual(members("JOINT VENTURE OF KHAN KON CHI CONSTRUCTION & DEVELOPMENT CORPORATION AND MRBII CONSTRUCTION CORPORATION"),
                         ["KHAN KON CHI CONSTRUCTION & DEVELOPMENT CORPORATION", "MRBII CONSTRUCTION CORPORATION"])

    def test_three_members(self):
        self.assertEqual(members("AUTRE PORTE TECHNIQUE GLOBAL INC., MULTI- SCAN CORPORATION & OPUSLAND INC. - JOINT VENTURE (AMSCO-JV)"),
                         ["AUTRE PORTE TECHNIQUE GLOBAL INC.", "MULTI- SCAN CORPORATION", "OPUSLAND INC."])

    def test_no_leftover_legal_form_part(self):
        found = members("JOINT VENTURE OF NTT DATA PHILIPPINES, INC. AND NTT DATA CORPORATION")
        self.assertEqual(found, ["NTT DATA PHILIPPINES, INC.", "NTT DATA CORPORATION"])

    def test_short_form_of_known_name(self):
        self.assertEqual(KNOWN.match("MEDILINES"), ("MEDILINES DISTRIBUTORS INCORPORATED", "prefix"))

    def test_ampersand_name_not_split_when_it_is_one_contractor(self):
        self.assertEqual(KNOWN.match("A & B BUILDERS")[1], "exact")


class DpwhTest(unittest.TestCase):
    def test_names_and_former_names(self):
        n = "BLACK HARDHAT INC. (FORMERLY: JCHARDHAT INC.)"
        self.assertEqual(dpwh_name(n), "BLACK HARDHAT INC.")
        self.assertEqual(dpwh_former(n), "JCHARDHAT INC.")
        self.assertEqual(dpwh_name("MRB11 CONSTRUCTION CORPORATION (FORMERLY:MRB CONS"), "MRB11 CONSTRUCTION CORPORATION")

    def test_pairing(self):
        self.assertIsNotNone(pair_with_dpwh(["A.M. ORETA", "ALLENCON"],
                                            [("A.M. ORETA & CO., INC.", "108"), ("ALLENCON DEVELOPMENT CORPORATION", "12302")]))
        self.assertIsNotNone(pair_with_dpwh(["TOPNOTCH CATALYST BUILDERS INC.", "JCHARDHAT INC."],
                                            [("TOPNOTCH CATALYST BUILDERS INC.", "34061"),
                                             ("BLACK HARDHAT INC. (FORMERLY: JCHARDHAT INC.)", "49520")]))
        self.assertIsNone(pair_with_dpwh(["RODEKOM GENERAL CONSTRUCTION AND ENTERPRISE", "TAGEL CORP."],
                                         [("F.B. BANTALES ENGINEERING CONSTRUCTION", "31045"), ("TAGEL CORPORATION", "43554")]))


if __name__ == "__main__":
    unittest.main()
