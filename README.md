# 📖 Guide utilisateur — Discord Team Builder Bot

Ce bot vous aide à **former des équipes équilibrées** (ou aléatoires), à **créer des salons vocaux** et à **gérer des niveaux** pour vos jeux (LoL, etc.).

---

## 👥 Commandes principales

### `/team`
Créer des équipes à partir des joueurs du **salon vocal** de l’auteur (ou d’une liste de mentions).  
**Options :**
- `mode` : `balanced` (par défaut) ou `random`
- `team_count` : nombre d’équipes (2–6)
- `sizes` : tailles fixes, ex. `3/3/2` (la somme doit = nb de joueurs)
- `with_groups` : regrouper des joueurs, ex. `@A @B | @C @D`
- `avoid_pairs` : séparer des paires, ex. `@A @B ; @C @D`
- `members` : mentions si vous n’êtes pas en vocal
- `create_voice` : `true` pour créer **Team 1..K** et y déplacer les joueurs
- `channel_ttl` : durée de vie des salons (minutes, défaut 90)
- `auto_import_riot` : `true/false` — si des joueurs ont lié LoL et que l’API est dispo

**Notes :**
- Les **ratings** viennent d’abord de la BDD, puis (si activé) d’un import Riot, sinon **1000** par défaut.
- Les contraintes sont respectées au mieux : le bot vous signale si certaines paires **n’ont pas pu être séparées**.

**Exemples :**
- `/team` (équilibré, 2 équipes à partir du vocal)
- `/team mode:random team_count:3`
- `/team sizes:"3/3/2" with_groups:"@A @B | @C @D" avoid_pairs:"@X @Y"`
- `/team members:"@A @B @C @D @E @F" create_voice:true channel_ttl:60`

---

### `/setskill`
Définir un **rating manuel** pour un joueur.  
Ex : `/setskill user:@Alice rating:1320`

### `/setrank`
Définir un **rang LoL offline** (sans API Riot) pour calculer un rating.  
Paramètres : `tier` (Gold, Emerald…), `division` (I/II/III/IV, vide si Master+), `lp` (0–100).  
Ex : `/setrank user:@Alice tier:Emerald division:III lp:9` → rating calculé.

### `/linklol`
Lier un compte LoL (et importer le rang si une **RIOT_API_KEY** est configurée côté serveur).  
Ex : `/linklol user:@Alice summoner:"MonPseudo" region:EUW`  
- Avec API : rating calculé automatiquement + rang mémorisé.  
- Sans API : le lien est stocké, mais utilisez `/setrank` ou `/setskill`.

### `/ranks`
Lister les **ratings** en BDD, avec affichage du **rang LoL** s’il est connu (_Emerald III 9 LP_).  
Options : `scope` (auto/voice/server), `sort` (rating_desc/rating_asc/name), `limit` (5–100).  
Ex : `/ranks` (auto), `/ranks scope:voice sort:name limit:50`

### `/disbandteams`
Supprimer les **salons vocaux temporaires** créés par `/team`.

---

## 🔐 Commandes admin / owner
- `/whoami` → affiche votre **User ID**.
- `/resync` → resynchroniser les commandes (utile après déploiement).
- `/restart` → redémarrer le bot (sur Railway, le process se coupe puis est relancé par la plateforme).
- `/shutdown` → arrêter le bot.

> L’accès admin est accordé aux **Admins du serveur** ou à l’**OWNER_ID** configuré côté serveur.

---

## ℹ️ À propos des ratings & rangs LoL
- Le bot stocke pour chaque joueur un **rating** numérique (ex. 1320) et, si dispo, un **rang LoL** (tier/division/LP) pour l’affichage.  
- Le **rating** est calculé à partir du rang (barème simple) ou fixé manuellement via `/setskill`.  
- Si un joueur n’a aucune info, le bot utilise **1000** par défaut (vous pouvez ensuite corriger).

---

## ❓Dépannage rapide
- **La commande n’apparaît pas ?** Demandez à un admin de lancer `/resync`. Vérifiez que l’intégration du bot est autorisée dans le salon.
- **Le bot ne crée pas les salons vocaux ?** Vérifiez qu’il a les permissions *Manage Channels* et *Move Members* sur le serveur/salon.
- **Mon rang LoL n’apparaît pas dans `/ranks` ?** Liez votre compte via `/linklol` (si l’API est en place) **ou** utilisez `/setrank`.

Bonne game ! 🎮
