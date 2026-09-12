import random
import unittest
from types import SimpleNamespace

from app.lanes import ROLES, assign_lanes, parse_roles, role_cost, select_teams, signature


class LaneTests(unittest.TestCase):
    def setUp(self):
        self.members = [SimpleNamespace(id=i, display_name=f"Player {i}") for i in range(10)]
        self.ratings = {i: (i + 1) * 100 for i in range(10)}
        self.preferences = {i: [ROLES[i % 5]] for i in range(10)}

    def select(self, mode="random", **kwargs):
        return select_teams(self.members, kwargs.pop("ratings", self.ratings), [5, 5],
                            kwargs.pop("groups", [[m] for m in self.members]),
                            kwargs.pop("avoid_pairs", set()),
                            kwargs.pop("preferences", self.preferences), mode, **kwargs)

    def test_both_modes_assign_one_player_per_lane_and_keep_mains(self):
        for mode in ("random", "balanced"):
            teams, assignments, violations = self.select(mode)
            self.assertFalse(violations)
            self.assertEqual(sorted(m.id for t in teams for m in t), list(range(10)))
            for team in teams:
                self.assertEqual({assignments[m.id] for m in team}, set(ROLES))
                self.assertTrue(all(assignments[m.id] == self.preferences[m.id][0] for m in team))

    def test_random_is_independent_of_elo_even_with_history(self):
        random.seed(123)
        first = self.select()
        history = {signature(first[0])}
        pairs = {(0, 1): 12}
        random.seed(456)
        a = self.select(seen_signatures=history, pair_counts=pairs)
        random.seed(456)
        b = self.select(ratings={}, seen_signatures=history, pair_counts=pairs)
        self.assertEqual(signature(a[0]), signature(b[0]))
        self.assertEqual(a[1], b[1])

    def test_balanced_finds_minimum_elo_gap_without_sacrificing_mains(self):
        teams, _, _ = self.select("balanced")
        gap = abs(sum(self.ratings[m.id] for m in teams[0]) - sum(self.ratings[m.id] for m in teams[1]))
        # One of each paired main: all five pairwise rating differences are 500.
        self.assertEqual(gap, 500)

    def test_secondary_and_extra_roles_avoid_offrole(self):
        team = self.members[:5]
        prefs = {0: ["mid", "top"], 1: ["jgl"], 2: ["mid"],
                 3: ["mid", "jgl", "bot"], 4: ["sup"]}
        score, assignment = assign_lanes(team, prefs)
        self.assertEqual(score, (0, 3))
        self.assertEqual(assignment[0], "top")
        self.assertEqual(assignment[3], "bot")

    def test_unavoidable_offroles_are_distributed_evenly(self):
        prefs = {i: ["mid"] for i in range(10)}
        teams, assignments, _ = self.select(preferences=prefs)
        self.assertEqual([sum(role_cost(prefs[m.id], assignments[m.id])[0] for m in t) for t in teams], [4, 4])

    def test_unknown_preferences_are_neutral(self):
        self.assertEqual(role_cost([], "top"), (0, 0))
        teams, assignments, _ = self.select(preferences={})
        self.assertEqual(len(assignments), 10)

    def test_constraints_apply_in_both_modes(self):
        groups = [self.members[:2], *[[m] for m in self.members[2:]]]
        for mode in ("random", "balanced"):
            teams, _, violations = self.select(mode, groups=groups, avoid_pairs={(0, 5)})
            locations = {m.id: i for i, team in enumerate(teams) for m in team}
            self.assertEqual(locations[0], locations[1])
            self.assertNotEqual(locations[0], locations[5])
            self.assertFalse(violations)

    def test_impossible_groups_fail_clearly(self):
        with self.assertRaises(ValueError):
            self.select(groups=[self.members[:6], *[[m] for m in self.members[6:]]])

    def test_history_does_not_override_role_quality(self):
        teams, _, _ = self.select()
        teams, assignments, _ = self.select(seen_signatures={signature(teams)})
        self.assertTrue(all(assignments[m.id] in self.preferences[m.id] for t in teams for m in t))

    def test_non_5v5_does_not_assign_lanes(self):
        teams, assignments, _ = select_teams(self.members, {}, [3, 3, 4],
                                            [[m] for m in self.members], set(), self.preferences, "random")
        self.assertEqual([len(t) for t in teams], [3, 3, 4])
        self.assertFalse(assignments)

    def test_invalid_preferences_rejected(self):
        for text in ("mid mid", "adc", "", "top jgl mid bot sup mid"):
            with self.assertRaises(ValueError):
                parse_roles(text)


if __name__ == "__main__":
    unittest.main()
