# app/tournament_logic.py
from __future__ import annotations
from typing import List, Tuple, Optional

def next_power_of_two(n: int) -> int:
    p = 1
    while p < n: p <<= 1
    return p

def seed_pairs(user_ids_by_seed: List[int]) -> List[Tuple[Optional[int], Optional[int]]]:
    """
    Retourne les paires du 1er tour type "1 vs N, 2 vs N-1, ..." avec BYE si besoin.
    """
    n = len(user_ids_by_seed)
    size = next_power_of_two(n)
    # complète avec None (BYE)
    padded = user_ids_by_seed + [None] * (size - n)
    pairs = []
    left, right = 0, size - 1
    while left < right:
        pairs.append((padded[left], padded[right]))
        left += 1
        right -= 1
    return pairs

def link_rounds(first_round_count: int) -> List[List[Tuple[int, int]]]:
    """
    Pour chaque round, donne les liens (match courant) -> (match suivant, slot).
    Round 1 a 'first_round_count' matches. Exemple:
      round_links[0][i] = (id_du_match_suivant, slot_1_ou_2)
    On ne connaît pas les IDs SQL ici ; on renverra un schéma positionnel.
    """
    links: List[List[Tuple[int,int]]] = []
    m = first_round_count
    while m > 1:
        round_links = []
        next_m = m // 2
        for i in range(m):
            next_match = i // 2
            slot = 1 if i % 2 == 0 else 2
            round_links.append((next_match, slot))
        links.append(round_links)
        m = next_m
    return links  # dernière round = finale (pas de next)

def build_bracket_matches(user_ids_by_seed: List[int], best_of: int = 1) -> List[dict]:
    """
    Construit une liste de "match dict" prêts pour l'insert DB (sans next_match_id finalisé).
    Les next_* sont donnés par indices positionnels et résolus au second passage.
    """
    pairs = seed_pairs(user_ids_by_seed)
    round_count = 1
    matches: List[dict] = []

    # Round 1
    for i, (p1, p2) in enumerate(pairs):
        matches.append({
            "round": 1,
            "pos_in_round": i,
            "p1_user_id": p1,
            "p2_user_id": p2,
            "best_of": best_of,
            "status": "open" if (p1 and p2) else ("done" if (p1 or p2) else "done"),  # BYE -> done
            "next_match_pos": None, "next_slot": None,
        })

    # Liens vers rounds suivants (positionnels)
    current_round_count = len(pairs)
    links = link_rounds(current_round_count)
    round_index = 0
    while current_round_count > 1:
        next_round_count = current_round_count // 2
        # créer les matches de la round suivante
        for j in range(next_round_count):
            matches.append({
                "round": round_index + 2,
                "pos_in_round": j,
                "p1_user_id": None, "p2_user_id": None,
                "best_of": best_of,
                "status": "pending",
                "next_match_pos": None, "next_slot": None,
            })
        # attacher les liens depuis la round précédente vers celle-ci
        prev_round_links = links[round_index]  # len = current_round_count
        for i, (n_pos, slot) in enumerate(prev_round_links):
            # trouver l'index SQL-like du "match précédent"
            prev_idx = sum((len(pairs) // (2**k)) for k in range(round_index)) + i
            # trouver l'index du match suivant (dans la zone qu'on vient d'ajouter)
            next_base = sum((len(pairs) // (2**k)) for k in range(round_index+1))
            next_idx = next_base + n_pos
            matches[prev_idx]["next_match_pos"] = next_idx
            matches[prev_idx]["next_slot"] = slot

        current_round_count = next_round_count
        round_index += 1

    return matches

def resolve_next_ids(sql_ids: List[int], matches: List[dict]) -> List[dict]:
    """
    On remplace next_match_pos par next_match_id (SQL) après l'insertion.
    sql_ids doit être dans le même ordre que 'matches'.
    """
    id_by_pos = {pos: mid for pos, mid in enumerate(sql_ids)}
    out = []
    for pos, m in enumerate(matches):
        nm_pos = m.get("next_match_pos")
        out.append({
            **m,
            "next_match_id": (id_by_pos[nm_pos] if nm_pos is not None else None),
            "next_slot": m.get("next_slot"),
        })
    return out
