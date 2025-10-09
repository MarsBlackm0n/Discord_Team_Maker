# ⚙️ SETUP technique

## 1) Installation rapide
```bash
git clone <repo>
cd <repo>
python -m venv .venv
source .venv/bin/activate  # ou .venv\Scripts\activate sur Windows
pip install -r requirements.txt
```

Créez `.env` :
```env
DISCORD_BOT_TOKEN=xxxxx
OWNER_ID=123456789012345678
GUILD_ID=123456789012345678
RIOT_API_KEY=
DB_PATH=skills.db
RESTART_MODE=self
```

Lancement :
```bash
python main.py
```

---

## 2) Commandes clés
- `/team` → crée des équipes équilibrées ou aléatoires  
- `/teamroll` → crée une combinaison inédite (bouton 🎲 pour reroll)  
- `/go` → crée/réutilise les salons vocaux Team 1..K et déplace les joueurs  
- `/tournament` → crée et gère un bracket  
- `/ranks`, `/setskill`, `/setrank`, `/linklol` → gèrent les ratings  
- `/help` → guide intégré (affiche le flow typique)

---

## 3) Architecture
```
app/
├─ bot.py              # création et sync globale
├─ voice.py            # salons vocaux (réutilisation + TTL reset)
├─ cogs/
│  ├─ team.py          # /team /teamroll /go /disbandteams
│  ├─ tournament.py    # /tournament*
│  ├─ help.py          # /help
│  ├─ ratings.py       # /setskill /ranks /linklol
│  └─ admin.py         # /resync /backupdb /shutdown etc.
```

---

## 4) Nouveau workflow typique

1️⃣ `/team` — crée les équipes  
2️⃣ `/teamroll` — reroll pour variété  
3️⃣ `/go` — lance la partie (salons + move)  
4️⃣ `/tournament` — si besoin d’un bracket  
5️⃣ `/disbandteams` — nettoyage  

Le bot conserve la **dernière configuration d’équipes** pour la relancer facilement avec `/go`.

---

## 5) Déploiement
Sur Railway : définissez `DB_PATH=/data/skills.db` et un volume `/data`.  
Sur local : tout fonctionne en SQLite.

---

Prêt à jouer ⚔️




# 📖 Guide utilisateur — Discord Team Builder & Tournament Bot

Ce bot vous aide à **former des équipes équilibrées**, à **créer ou réutiliser des salons vocaux**, à **gérer vos niveaux/rangs LoL**, et à **organiser des tournois**.

---

## 💡 Exemple de flow typique

1️⃣ `/team` — crée les équipes (équilibrées ou aléatoires)  
2️⃣ `/teamroll` — génère une version inédite (tout le monde joue avec d’autres coéquipiers)  
3️⃣ `/go` — crée/réutilise les salons *Team 1..K* et déplace automatiquement les joueurs  
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
Génère une **nouvelle combinaison inédite** (chaque paire de joueurs est suivie en base).  
- Paramètre `session` pour identifier la série de rolls.  
- Le bouton 🎲 “Reroll” permet de relancer instantanément une combinaison.  
- Les combinaisons sont stockées pour éviter les répétitions.

---

### `/go`
Lance la **phase de jeu** :
- Crée ou réutilise les salons “Team 1..K” selon la dernière configuration (`/team` ou `/teamroll`).  
- Déplace automatiquement les joueurs.  
- Le TTL des salons créés est **réinitialisé** à chaque `/go`.

---

### `/tournament`
Permet de gérer un **tournoi à élimination simple** :
- `/tournament create`, `/add`, `/start`, `/view`, `/report`, `/cancel`.

---

### `/disbandteams`
Supprime les salons vocaux créés par le bot encore existants.

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
Les salons créés par le bot sont supprimés automatiquement après la durée (`channel_ttl`), sauf s’ils sont réutilisés (le TTL est alors remis à zéro).

---

Bonne game 🎮

