# 🚂 Déployer le bot sur Railway (gratuit)

Ce dépôt est prêt pour un déploiement **Railway**. On utilise un **Volume** pour persister la base SQLite, et des **variables d’environnement** pour la config. Ajouts récents : **tournoi**, **/help**, **backup/export CSV**, **réutilisation des salons Team i**.

---

## 1) Pré-requis côté repo
- `main.py` à la racine (point d’entrée).
- `app/` avec cogs & modules : `cogs/` (`help.py`, `admin.py`, `ratings.py`, `team.py`, `tournament.py`), `db.py`, `riot.py`, `team_logic.py`, `voice.py`, `tournament_logic.py`, `config.py`.
- `requirements.txt` : `discord.py`, `aiohttp`, `aiosqlite`, `python-dotenv`.
- *(optionnel)* `runtime.txt` avec `python-3.11.9` (ou définissez la variable `PYTHON_VERSION` côté Railway).
- **Ne commitez pas votre `.env`** (gardez-le localement).

---

## 2) Créer le projet Railway depuis GitHub
1. Railway → **New Project → Deploy from GitHub** → choisissez votre repo.
2. Dans le **service** (worker Python), vérifiez **Start Command** : `python main.py`.  
   > Railway/Nixpacks détecte Python automatiquement ; corrigez si besoin.

---

## 3) Variables d’environnement (Service → Variables)
À définir au minimum :
- `DISCORD_BOT_TOKEN=...`
- `DB_PATH=/data/skills.db` ← chemin de la base sur le volume
- `RESTART_MODE=manager` ← pour que `/restart` laisse Railway relancer l’app
- `GUILD_ID=xxxxxxxxxxxx` ← ID de votre serveur pour une **sync slash instantanée**
- *(optionnel)* `OWNER_ID=xxxxxxxxxxxx` (pour les commandes admin)
- *(optionnel)* `RIOT_API_KEY=...` (sinon le bot fonctionne en **offline** pour LoL)
- *(recommandé)* `PYTHON_VERSION=3.11.9`

> ⚠️ Une **clé de développement** générée sur le [Developer Portal](https://developer.riotgames.com/) expire au
> bout de **24h** : il faudra régénérer `RIOT_API_KEY` sur Railway régulièrement. Sans clé valide, `/linklol`,
> `/syncrank` et l'auto-import répondent simplement par un message d'erreur — le reste du bot continue de
> fonctionner normalement (rating manuel via `/setrank`/`/setskill`).

> Astuce : récupérez votre **User ID** avec `/whoami`.

---

## 4) Créer et monter un **Volume** (persistance DB)
Sur la **Project Canvas** Railway :  
- **⌘K / Ctrl+K** → **“Volume”** → **Create Volume**.  
- Attachez-le à votre **service**.  
- **Mount path** : `/data`  
- Sauvegardez.  
Ensuite, ajoutez/validez `DB_PATH=/data/skills.db` dans les **Variables**.

> Sans volume, `skills.db` est **éphémère** (perdu au rebuild / redeploy).

---

## 5) Intents & permissions
- Portail Discord → **App → Bot → Privileged Gateway Intents** : activez **Server Members Intent**.  
  *(Optionnel)* **Message Content Intent** pour supprimer le warning si vous utilisez `commands.Bot`.
- Permissions serveur : *Use Application Commands*, *Manage Channels*, *Move Members* (et *Embed Links*).

---

## 6) Déployer & vérifier
- Lancez un **Deploy/Redeploy**.
- Logs attendus :  
  `✅ Connecté… — slash prêts. DB: /data/skills.db` et `✅ Loaded app.cogs.*`

Si les commandes n’apparaissent pas :  
- Vérifiez `GUILD_ID`.  
- Exécutez `/resync` (admin).  
- Vérifiez les permissions du bot sur le serveur/salon.

---

## 7) Cycle de vie & stockage
- `/restart` : coupe le process ; Railway le relance automatiquement (**manager**).
- `/shutdown` : arrête le bot (peut être relancé selon la policy).
- **Vocal Team i** : le bot **réutilise** les salons existants (noms stricts) et **supprime après TTL** uniquement ceux qu’il a créés.
- **Sauvegarde/Export** :  
  - `/backupdb` → envoie le fichier **.db**.  
  - `/exportcsv` → envoie un **ZIP** de CSV (toutes les tables).  
  Si la pièce jointe est trop grosse, passez par **Open Shell** (Service → Deployments → Shell) :
  ```bash
  ls -lah /data
  nix-env -iA nixpkgs.sqlite  # installer sqlite (si besoin)
  sqlite3 /data/skills.db ".tables"
  sqlite3 /data/skills.db "SELECT * FROM skills LIMIT 5;"
  ```

---

## 8) Dépannage rapide
- **Slash absents** : `GUILD_ID` incorrect/manquant, sync non faite, intégrations désactivées, ou redeploy manquant.
- **BDD non persistée** : volume non monté sur `/data` **ou** `DB_PATH` non défini.
- **Vocal non créé/déplacé** : permissions *Manage Channels* & *Move Members*.
