# 📖 Guide utilisateur — Discord Team Builder & Tournament Bot

Ce bot vous aide à **former des équipes équilibrées** (ou aléatoires), à **créer/réutiliser des salons vocaux**, à **gérer vos niveaux / rangs LoL**, **et maintenant à organiser des tournois** (arbre à élimination simple).

---

## 🆘 Aide intégrée
### `/help`
- Affiche l’aide générale (par défaut en réponse *ephemeral*).
- Exemple : `/help command:team` pour l’aide ciblée d’une commande.

---

## 👥 Formation d’équipes

### `/team`
Crée des équipes à partir des joueurs du **salon vocal** de l’auteur (ou d’une liste de mentions).  
**Options :**
- `mode` : `balanced` (par défaut) ou `random`
- `team_count` : 2–6 équipes
- `sizes` : tailles fixes, ex. `3/3/2` (la somme doit = nb de joueurs)
- `with_groups` : grouper des joueurs, ex. `@A @B | @C @D`
- `avoid_pairs` : séparer des paires, ex. `@A @B ; @C @D`
- `members` : mentions si vous n’êtes pas en vocal
- `create_voice` : `true` → **Team 1..K** (réutilise les salons existants si nommés pareil)
- `channel_ttl` : durée de vie des salons **créés par le bot** (minutes, défaut 90)
- `auto_import_riot` : `true/false` — importe le rang via Riot si possible

**Notes :**
- Les **ratings** viennent d’abord de la BDD, puis (si activé) d’un import Riot, sinon **1000** par défaut.
- Les contraintes sont respectées au mieux : le bot signale si certaines paires **n’ont pas pu être séparées**.
- **Salons vocaux** : si des salons nommés **“Team 1”, “Team 2”, …** existent déjà, le bot **les réutilise** et ne crée que les manquants. Le **cleanup** (suppression après TTL) ne concerne **que** les salons créés par le bot.

**Exemples :**
- `/team` (équilibré, 2 équipes à partir du vocal)
- `/team mode:random team_count:3`
- `/team sizes:"3/3/2" with_groups:"@A @B | @C @D" avoid_pairs:"@X @Y"`
- `/team members:"@A @B @C @D @E @F" create_voice:true channel_ttl:60`

### `/disbandteams`
Supprime les **salons vocaux temporaires** créés par le bot (ceux encore existants).

---

## 📊 Ratings & LoL

### `/setskill`
Définir un **rating manuel** pour un joueur.  
Ex : `/setskill user:@Alice rating:1320`

### `/setrank`
Définir un **rang LoL offline** (sans API Riot) pour calculer un rating.  
Paramètres : `tier` (Gold, Emerald…), `division` (I/II/III/IV, vide si Master+), `lp` (0–100).  
Ex : `/setrank user:@Alice tier:Emerald division:III lp:9`

### `/linklol`
Lier un compte LoL (et importer le rang si une **RIOT_API_KEY** est configurée côté serveur).  
Ex : `/linklol user:@Alice summoner:"MonPseudo" region:EUW`  
- Avec API : rating calculé automatiquement + rang mémorisé (SoloQ).  
- Sans API : le lien est stocké, utilisez `/setrank` ou `/setskill`.

### `/ranks`
Lister les **ratings** en BDD, avec affichage du **rang LoL** (_Emerald III 9 LP_) s’il est connu.  
Options : `scope` (auto/voice/server), `sort` (rating_desc/rating_asc/name), `limit` (5–100).

---

## 🏆 Tournoi (Single Elimination)
Groupe de commandes : **`/tournament`**

- `/tournament create name:"Clash #1"` — crée un tournoi (état **setup**).
- `/tournament add [members:"@A ..."]` — ajoute des joueurs (mentions ou **membres du vocal** de l’auteur si vide). **Seeding automatique** par rating (décroissant).
- `/tournament start [best_of:1]` — génère le bracket (BYE auto si non puissance de 2) et passe en **running**.
- `/tournament view` — affiche le bracket (par rounds) dans le salon courant.
- `/tournament report match_id:12 winner:@Alice p1_score:1 p2_score:0` — enregistre un résultat et **fait avancer** le vainqueur.
- `/tournament cancel` — annule le tournoi actif.

> Modèle actuel : **élimination simple** (1 arbre). Extensions possibles : double élimination, round-robin, équipes, etc.

---

## 🔐 Commandes admin / owner
- `/whoami` → affiche votre **User ID**.
- `/resync` → resynchronise les commandes (utile après déploiement).
- `/restart` → redémarre le bot (sur Railway, le process est relancé par la plateforme).
- `/shutdown` → arrête le bot.
- `/backupdb` → **télécharge le fichier SQLite** (`skills.db`) en pièce jointe.
- `/exportcsv` → exporte **toutes les tables** en **CSV** (un fichier par table) dans un **ZIP**.

> L’accès admin est accordé aux **Admins du serveur** ou à l’**OWNER_ID** configuré côté serveur.

---

## ❓Dépannage rapide
- **La commande n’apparaît pas ?** Demandez à un admin de lancer `/resync`. Vérifiez l’intégration du bot dans le salon.
- **Le bot ne crée pas / ne supprime pas les salons vocaux ?** Vérifiez *Manage Channels* et *Move Members*. Seuls les salons créés par le bot sont supprimés après TTL.
- **Mon rang LoL n’apparaît pas dans `/ranks` ?** Liez votre compte via `/linklol` (si l’API est en place) **ou** utilisez `/setrank`.

Bonne game ! 🎮
