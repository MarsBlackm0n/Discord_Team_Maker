# ⚙️ SETUP technique (local & Discord)

Ce guide couvre la configuration **locale**, la **création du bot** côté Discord et les **permissions** nécessaires.  
Le projet est structuré en **modules/cogs** pour faciliter la maintenance.

---

## 1) Installation locale
```bash
git clone <votre-repo>
cd <votre-repo>
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
```

Créez un fichier `.env` **à la racine** (non commité) :
```env
DISCORD_BOT_TOKEN=xxxxx
OWNER_ID=123456789012345678     # (optionnel)
GUILD_ID=123456789012345678     # pour sync immédiate sur VOTRE serveur
RIOT_API_KEY=                   # (optionnel)
DB_PATH=skills.db               # en local ; en prod => /data/skills.db
RESTART_MODE=self               # local: self ; Railway: manager
```

Lancez le bot :
```bash
python main.py
```
- Arrêt : **Ctrl+C**
- Test rapide : `/whoami`, `/help`, `/team`, `/ranks`

---

## 2) Créer l’application & le bot (Developer Portal)
1. **New Application** → nommez-la.
2. Onglet **Bot** → **Add Bot** → copiez le token (`DISCORD_BOT_TOKEN`).
3. **Intents** : activez **Server Members Intent**. *(Optionnel)* **Message Content**.
4. **OAuth2 → URL Generator** : Scopes `bot`, `applications.commands` ; Permissions : *Use Application Commands*, *Manage Channels*, *Move Members*, *Embed Links*.

Invitez le bot avec l’URL générée sur votre serveur. Si les commandes tardent, utilisez `/resync` et vérifiez `GUILD_ID`.

---

## 3) Architecture du projet
```
.
├─ main.py                  # point d’entrée : crée et lance le bot
└─ app/
   ├─ bot.py                # création du bot, chargement des cogs, sync slash
   ├─ config.py             # lecture env (Settings)
   ├─ db.py                 # SQLite + schéma + accès (skills, lol, tournoi)
   ├─ riot.py               # intégration Riot + conversion rang→rating
   ├─ team_logic.py         # algos de répartition + parsing contraintes
   ├─ tournament_logic.py   # génération bracket single-elim
   ├─ voice.py              # création/réutilisation/suppression salons
   └─ cogs/
      ├─ help.py            # /help
      ├─ admin.py           # /whoami /resync /restart /shutdown /backupdb /exportcsv
      ├─ ratings.py         # /setskill /setrank /linklol /ranks
      ├─ team.py            # /team /disbandteams
      └─ tournament.py      # /tournament create/add/start/view/report/cancel
```

---

## 4) Riot API (optionnel)
- Sans clé, le bot fonctionne en **offline** : `/setrank` ou `/setskill`.
- Avec `RIOT_API_KEY`, `/linklol` importe le rang **SoloQ** et calcule le rating.

---

## 5) Conseils & debug
- Le log au démarrage affiche : `✅ Connecté ... — slash prêts. DB: ...` et `✅ Loaded app.cogs.*`.
- `/resync` (admin) force la resynchronisation des commandes.
- Si un cog ne charge pas, vérifiez les logs (traceback) et la version Python (`PYTHON_VERSION=3.11.9` recommandé).
- Sur Railway, assurez `DB_PATH=/data/skills.db` et un **Volume** monté sur `/data`.

Pour l’hébergement, suivez **DEPLOY_RAILWAY.md**. Bon dev !
