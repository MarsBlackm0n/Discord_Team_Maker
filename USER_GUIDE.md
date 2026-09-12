# 📖 Guide utilisateur — Discord Team Builder & Tournament Bot

Ce bot vous aide à **former des équipes équilibrées**, à **créer ou réutiliser des salons vocaux**, à **gérer vos niveaux/rangs LoL**, et à **organiser des tournois**.

---

## 💡 Exemple de flow typique

1️⃣ `/team` — crée les équipes (équilibrées ou aléatoires)  
2️⃣ `/teamroll` — relance la génération en favorisant la variété à qualité égale  
3️⃣ `/move` — crée/réutilise les salons *Team 1..K* et déplace automatiquement les joueurs  
4️⃣ `/tournament create` → `/tournament add/start/view` — gère le bracket  
5️⃣ `/disbandteams` — supprime les salons créés à la fin

---

## 👥 Commandes principales

### `/team`
Crée des équipes équilibrées ou aléatoires à partir du salon vocal ou d’une liste de mentions.  
- `team_count`: 2–6  
- `mode`: `balanced` (défaut) ou `random`  
- `with_groups`, `avoid_pairs`, `sizes`, etc.  
- `create_voice:true` pour lancer immédiatement la création des salons  
- Le bot réutilise automatiquement les salons déjà nommés *Team 1…K*.

---

### `/teamroll`
Recherche une composition différente parmi les meilleures pour les rôles et, en mode balanced, pour l’ELO. Une composition peut revenir si les alternatives sont moins bonnes (chaque paire de joueurs est suivie en base).  
- Paramètre `session` pour identifier la série de rolls.  
- Le bouton 🎲 “Reroll” permet de relancer instantanément une combinaison.  
- Les combinaisons sont stockées pour éviter les répétitions.

---

### `/move`
Lance la **phase de jeu** :
- Crée ou réutilise les salons “Team 1..K” selon la dernière configuration (`/team` ou `/teamroll`).  
- Déplace automatiquement les joueurs.  
- Le TTL des salons créés est **réinitialisé** à chaque `/move`.

---

### `/tournament`
Permet de gérer un **tournoi à élimination simple** :
- `/tournament create`, `/add`, `/start`, `/view`, `/report`, `/cancel`.

---

### `/disbandteams`
Supprime les salons vocaux temporaires vides ; les salons occupés sont conservés.

---

### `/ranks`, `/setskill`, `/setrank`, `/linklol`
Gestion des **ratings** manuels ou importés via Riot.

---

### `/help`
Affiche l’aide et un exemple de workflow.  
Ex : `/help command:team`.

---

## 🧹 Permissions requises
- **Manage Channels**
- **Move Members**
- **Use Application Commands**

---

## 🕒 Expiration (TTL)
Les salons créés par le bot sont supprimés uniquement après `channel_ttl` minutes sans joueur (90 par défaut). Un salon occupé est conservé, même après plusieurs heures. Le délai repart à zéro lors des entrées/sorties et de la réutilisation du salon. Le nettoyage vérifie les salons toutes les 30 secondes. Les salons préexistants ne sont pas supprimés. Le suivi reste en mémoire : après un redémarrage du bot, les anciens salons ne sont plus nettoyés automatiquement.

---

Bonne game 🎮


## Préférences de rôles LoL (5 contre 5)

Chaque joueur peut enregistrer de 1 à 5 rôles, dans son ordre de préférence :

```text
/setroles first_role:mid second_role:top
/setroles first_role:bot second_role:mid third_role:sup
/roles
/roles user:@Joueur
```

Les choix proposés sont `top`, `jgl`, `mid`, `bot`, `sup`. Les doublons sont refusés.
Relancer `/setroles` remplace la liste précédente. La liste est sauvegardée par serveur,
et conservée au redémarrage. Pour modifier un autre joueur, utiliser `user:@Joueur`
avec la permission **Gérer le serveur**.

Avec exactement deux équipes de cinq, `/team`, `/teamroll` et le bouton **Reroll**
attribuent automatiquement un TOP, JGL, MID, BOT et SUP par équipe.
Pour les autres formats, le bot génère les équipes sans attribution de rôles.

La sélection compare toutes les 126 répartitions distinctes en 5 contre 5.
Les groupes à garder ensemble restent obligatoires, puis le bot minimise les violations
des paires à séparer. Parmi ces compositions, le mode `balanced` applique un seuil
`elo_gap` sur l’écart de **rating moyen par joueur** entre les deux équipes (50 par défaut).
Un seuil de 50 correspond donc à un écart de 250 sur les totaux des équipes de cinq.

Si au moins une composition respecte le seuil (écart inférieur ou égal), seules les
compositions sous ce seuil sont comparées, dans cet ordre :

1. Minimum de joueurs placés sur un rôle absent de leur liste.
2. Minimum de concessions : premier choix = 0, deuxième = 1, puis 2, 3, 4 ; hors liste = 5.
3. Répartition de ces concessions aussi égale que possible entre les équipes.
4. Minimum d’écart entre les ratings moyens.
5. À qualité égale : préférence pour les compositions inédites, puis les paires moins jouées lors des relances ; tirage aléatoire entre ex æquo.

Si aucune composition ne respecte le seuil avec les contraintes prioritaires, le bot
retient **le plus petit écart atteignable**, puis optimise les rôles et la variété.
Le résultat affiche le seuil, l’écart réel et un avertissement si le seuil est dépassé.
Cela peut entraîner des choix secondaires, tertiaires ou hors liste, signalés dans le résultat.

```text
/team mode:balanced elo_gap:50
/team mode:balanced elo_gap:25
/teamroll use_last:true elo_gap:75
```

Pour un groupe allant d’Argent à Diamant, commencer à **50** : l’échelle actuelle du bot
place Argent IV à 1000 et Diamant IV à 1400 (hors LP). **25** privilégie davantage la proximité
d’ELO ; **75** laisse plus de liberté aux postes. Ces valeurs sont des points de départ,
à ajuster selon les parties, pas une calibration statistique de vos joueurs.

Une valeur basse favorise un ELO proche ; une valeur plus haute laisse davantage de place
aux préférences de rôles. `0` recherche un écart nul et, si impossible, le minimum atteignable.
Les valeurs négatives sont refusées. `/teamroll` reprend le dernier seuil enregistré si
l’option est omise (50 pour les anciennes configurations) ; le bouton Reroll conserve
le seuil du tirage. `/team_last` affiche également le seuil et l’écart enregistrés.

Le mode `random` ignore complètement l’ELO et `elo_gap`, optimise les rôles puis la variété,
et n’importe pas de rang Riot. Pour les formats autres que 5v5, `elo_gap` ne s’applique pas.
Un joueur sans préférences est attribué sans pénalité, avec la mention **préférences inconnues** :
ce n’est pas une confirmation qu’il maîtrise tous les postes. Les rôles secondaires et les
placements **hors préférences** sont affichés dans le résultat. Renseigner les dix profils
permet donc un meilleur équilibre. L’ELO reste le rating global existant, pas un rating par rôle.

```text
/team mode:random create_voice:true
/team mode:balanced create_voice:true
```

Après une relance enregistrée, `/team_last` affiche aussi les rôles et `/move` utilise les nouvelles équipes.
Les relances ne déplacent pas automatiquement les joueurs : utiliser `/move` quand la composition convient.
