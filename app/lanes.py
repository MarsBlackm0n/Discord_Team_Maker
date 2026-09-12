"""Role-first team selection. Exact search for 5v5; bounded search otherwise."""
import itertools
import random

ROLES = ("top", "jgl", "mid", "bot", "sup")


def parse_roles(text):
    roles = text.lower().replace(",", " ").replace("/", " ").split()
    if not roles or len(roles) > 5 or len(set(roles)) != len(roles) or any(r not in ROLES for r in roles):
        raise ValueError("Indique 1 à 5 rôles distincts dans l'ordre : top, jgl, mid, bot, sup.")
    return roles


def role_cost(preferences, role):
    # Unknown preferences are neutral, and explicitly labelled in the result.
    if not preferences:
        return 0, 0
    if role not in preferences:
        return 1, 5
    return 0, preferences.index(role)


def assign_lanes(team, preferences):
    """Minimise off-role count, then preference rank; deterministic ties."""
    ordered = sorted(team, key=lambda m: m.id)
    best = None
    for roles in itertools.permutations(ROLES):
        costs = [role_cost(preferences.get(m.id, []), r) for m, r in zip(ordered, roles)]
        score = tuple(map(sum, zip(*costs)))
        if best is None or score < best[0]:
            best = score, {m.id: r for m, r in zip(ordered, roles)}
    return best


def signature(teams):
    return "|".join(sorted("-".join(str(i) for i in sorted(m.id for m in t)) for t in teams))


def select_teams(members, ratings, sizes, groups, avoid_pairs, preferences, mode,
                 attempts=200, seen_signatures=None, pair_counts=None):
    from .team_logic import balance_k_teams_with_constraints

    mode = mode.lower()
    if mode not in ("balanced", "random"):
        raise ValueError("Le mode doit être balanced ou random.")
    if not 2 <= len(sizes) <= 6 or any(s <= 0 for s in sizes) or sum(sizes) != len(members):
        raise ValueError("Les tailles doivent correspondre aux joueurs présents (2 à 6 équipes non vides).")
    seen_signatures = seen_signatures or set()
    pair_counts = pair_counts or {}
    lane_mode = sizes == [5, 5]
    cache = {}

    def candidates():
        if lane_mode:
            # Fix the first player to eliminate mirrored partitions: 126 candidates.
            for rest in itertools.combinations(members[1:], 4):
                first = [members[0], *rest]
                ids = {m.id for m in first}
                yield [first, [m for m in members if m.id not in ids]]
        else:
            for _ in range(max(50, min(5000, attempts))):
                shuffled = groups[:]
                random.shuffle(shuffled)
                scores = ratings if mode == "balanced" else {m.id: 0 for m in members}
                teams, _ = balance_k_teams_with_constraints(members, scores, len(sizes), sizes, shuffled, avoid_pairs)
                yield teams

    best_score, pool = None, []
    for teams in candidates():
        if [len(t) for t in teams] != sizes:
            continue
        locations = {m.id: i for i, t in enumerate(teams) for m in t}
        if any(len({locations[m.id] for m in g}) != 1 for g in groups):
            continue
        violations = sorted((a, b) for a, b in avoid_pairs if a in locations and b in locations and locations[a] == locations[b])
        assignments, costs = {}, []
        if lane_mode:
            for team in teams:
                key = tuple(sorted(m.id for m in team))
                if key not in cache:
                    cache[key] = assign_lanes(team, preferences)
                cost, assignment = cache[key]
                costs.append(cost)
                assignments.update(assignment)
        role_score = (sum(c[0] for c in costs), sum(c[1] for c in costs),
                      abs(costs[0][1] - costs[1][1])) if costs else (0, 0, 0)
        totals = [sum(ratings[m.id] for m in t) for t in teams] if mode == "balanced" else [0]
        repetition = sum(pair_counts.get(tuple(sorted((a.id, b.id))), 0)
                         for team in teams for a, b in itertools.combinations(team, 2))
        score = (len(violations), *role_score, max(totals) - min(totals),
                 signature(teams) in seen_signatures, repetition)
        if best_score is None or score < best_score:
            best_score, pool = score, [(teams, assignments, violations)]
        elif score == best_score:
            pool.append((teams, assignments, violations))
    if not pool:
        raise ValueError("Impossible de respecter ces tailles et groupes de joueurs. Réduis les groupes ou adapte les tailles.")
    teams, assignments, violations = random.choice(pool)
    if lane_mode:
        # Canonical enumeration must not always put the first player in Team 1.
        random.shuffle(teams)
    return teams, assignments, violations


def format_player(member, ratings, assignments, preferences, show_rating=True):
    text = member.display_name
    if show_rating:
        text += f" ({int(ratings[member.id])})"
    if member.id in assignments:
        role = assignments[member.id]
        prefs = preferences.get(member.id, [])
        label = "préférences inconnues" if not prefs else (
            f"choix {prefs.index(role) + 1}" if role in prefs else "⚠ hors préférences")
        text = f"**{role.upper()}** — {text} · {label}"
    return "- " + text
