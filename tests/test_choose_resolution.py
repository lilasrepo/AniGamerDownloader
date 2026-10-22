#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core.engine.choose_resolution -- the resolution selection rule.

Expected values are hand-worked from the rule in CLAUDE.md / the docstring:
exact match wins; else (lock on) the highest usable at-or-below ``wanted``;
else the lowest usable; nothing usable -> (None, permanent=True).
Lock off = nearest available, floor ignored.
"""

import itertools
import unittest

import tests  # noqa: F401  (installs the network guard before core.engine is imported)
from core.engine import choose_resolution


class ExactMatch(unittest.TestCase):
    def test_exact_match_wins(self):
        self.assertEqual(choose_resolution([360, 540, 720, 1080], 1080, 720, True), (1080, False))

    def test_exact_match_wins_with_lock_off(self):
        self.assertEqual(choose_resolution([360, 540, 720, 1080], 540, 0, False), (540, False))

    def test_wanted_as_config_string(self):
        # read_settings() normalises download_resolution to str
        self.assertEqual(choose_resolution([720, 1080], '1080', 720, True), (1080, False))

    def test_available_as_strings_returns_int(self):
        chosen, permanent = choose_resolution(['360', '720'], '720', 0, True)
        self.assertEqual((chosen, permanent), (720, False))
        self.assertIsInstance(chosen, int)

    def test_exact_match_below_floor_still_wins(self):
        # Documented order: "exact match wins" is checked before the floor.
        self.assertEqual(choose_resolution([360, 1080], 360, 720, True), (360, False))


class LockOnWithFloor(unittest.TestCase):
    def test_old_title_mastered_only_to_720(self):
        # 2004-era series: wants 1080, site tops out at 720, floor 720 -> 720
        self.assertEqual(choose_resolution([360, 540, 720], 1080, 720, True), (720, False))

    def test_highest_at_or_below_wanted(self):
        self.assertEqual(choose_resolution([360, 480, 540, 1080], 720, 0, True), (540, False))

    def test_highest_at_or_below_wanted_above_floor(self):
        # usable at-or-below 1080 with floor 360: 360, 540, 720 -> highest = 720
        self.assertEqual(choose_resolution([360, 540, 720, 1440], 1080, 360, True), (720, False))

    def test_degraded_guest_session_is_permanent(self):
        # guest session only ever offers ~360P -> refuse, retrying cannot help
        self.assertEqual(choose_resolution([360], 1080, 720, True), (None, True))

    def test_all_below_floor_is_permanent(self):
        self.assertEqual(choose_resolution([360, 480, 540], 1080, 720, True), (None, True))

    def test_nothing_at_or_below_wanted_takes_lowest_usable(self):
        # wants 720, only 1080 and 1440 above floor -> lowest usable = 1080
        self.assertEqual(choose_resolution([360, 1080, 1440], 720, 720, True), (1080, False))

    def test_floor_zero_means_no_floor(self):
        self.assertEqual(choose_resolution([360], 1080, 0, True), (360, False))

    def test_floor_equal_to_offer_is_usable(self):
        self.assertEqual(choose_resolution([540, 720], 1080, 720, True), (720, False))

    def test_duplicates_in_available(self):
        self.assertEqual(choose_resolution([720, 720, 360], 1080, 720, True), (720, False))


class LockOff(unittest.TestCase):
    def test_nearest_available(self):
        self.assertEqual(choose_resolution([360, 540, 720], 1080, 0, False), (720, False))

    def test_nearest_can_be_above_wanted(self):
        # |900-1080|=180 < |900-360|=540 -> 1080, above wanted
        self.assertEqual(choose_resolution([360, 1080], 900, 0, False), (1080, False))

    def test_floor_ignored_when_lock_off(self):
        # floor is "only consulted when lock"
        self.assertEqual(choose_resolution([360], 1080, 720, False), (360, False))


class EmptyOffer(unittest.TestCase):
    def test_empty_with_lock(self):
        self.assertEqual(choose_resolution([], 1080, 720, True), (None, True))

    def test_empty_without_lock(self):
        self.assertEqual(choose_resolution([], 1080, 0, False), (None, True))


class Invariants(unittest.TestCase):
    """Properties stated in the docstring, checked over a grid of inputs."""

    OFFERS = [[], [360], [720], [1080], [360, 720], [540, 1080], [360, 540, 720, 1080], [1080, 1440]]
    WANTED = [360, 540, 720, 1080]
    FLOORS = [0, 360, 720, 1080]

    def test_grid(self):
        for offer, wanted, floor, lock in itertools.product(
                self.OFFERS, self.WANTED, self.FLOORS, (True, False)):
            with self.subTest(offer=offer, wanted=wanted, floor=floor, lock=lock):
                chosen, permanent = choose_resolution(offer, wanted, floor, lock)
                # "chosen is None exactly when permanent is True"
                self.assertEqual(chosen is None, permanent)
                if chosen is not None:
                    self.assertIn(chosen, offer)
                    # lock on: anything returned clears the floor, unless it was the exact match
                    if lock and chosen != wanted:
                        self.assertGreaterEqual(chosen, floor)


if __name__ == '__main__':
    unittest.main()
