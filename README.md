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
