# 🚂 Déployer le bot sur Railway (gratuit)

Ce dépôt est prêt pour un déploiement **Railway**. On utilise un **Volume** pour persister la base SQLite, et des **variables d’environnement** pour la config.

---

## 1) Pré-requis côté repo
- `main.py` à la racine (fourni).
- `requirements.txt` avec: `discord.py`, `aiohttp`, `aiosqlite`, `python-dotenv` (et leurs versions si besoin).
- **Ne commitez pas votre `.env`** (gardez-le localement).

---

## 2) Créer le projet Railway depuis GitHub
1. Sur Railway → **New Project → Deploy from GitHub** → choisissez votre repo.
2. Dans votre **service** (bloc du canvas), vérifiez le **Start Command** : `python main.py`.  
   > Railway détecte en général Python automatiquement (Nixpacks). Corrigez si besoin.

---

## 3) Variables d’environnement (Service → Variables)
Ajoutez au minimum :
- `DISCORD_BOT_TOKEN=...`
- `RESTART_MODE=manager`  ← pour que `/restart` laisse Railway relancer l’app
- `DB_PATH=/data/skills.db` ← pour écrire la DB dans le volume
- `GUILD_ID=xxxxxxxxxxxx`  ← ID de **votre** serveur pour une sync slash **instantanée**
- *(optionnel)* `OWNER_ID=xxxxxxxxxxxx` (votre user id Discord pour les commandes admin)
- *(optionnel)* `RIOT_API_KEY=...` (sinon le bot bascule en mode “offline” pour les rangs LoL)

> Astuce : utilisez `/whoami` sur le bot pour récupérer votre **User ID**.

---

## 4) Créer et monter un **Volume** (persistance DB)
Sur la **Project Canvas** Railway :  
- **⌘K / Ctrl+K** → tapez **“Volume”** → **Create Volume**.  
- Attachez-le à **votre service** (le worker Python).  
- **Mount path** : `/data`  
- Sauvegardez.  
Ensuite, assurez-vous d’avoir la variable `DB_PATH=/data/skills.db` dans les **Variables**.

> Sans volume, `skills.db` sera éphémère (perdu au rebuild).

---

## 5) Déployer et vérifier
- Lancez un **Deploy/Redeploy**.
- Ouvrez les **Logs** : vous devriez voir quelque chose comme :  
  `✅ ... — slash prêts (synced guild: 123456...). DB: /data/skills.db`

Si les commandes n’apparaissent pas tout de suite :  
- Vérifiez `GUILD_ID`.  
- Exécutez la commande admin `/resync`.  
- Vérifiez les **Permissions** côté serveur (voir SETUP).

---

## 6) Cycle de vie
- `/restart` : le bot s’arrête et Railway le relance (grâce à `RESTART_MODE=manager`).  
- `/shutdown` : arrête le process (Railway relancera si la policy l’autorise).
- Les **salons vocaux temporaires** créés par `/team ... create_voice:true` sont auto-supprimés après le TTL.

---

## 7) Dépannage rapide
- **Commandes slash absentes** : `GUILD_ID` manquant/incorrect, sync non faite, intégrations désactivées sur le serveur, ou pas de redeploy.
- **Création/move vocal en erreur** : donnez au bot les permissions *Manage Channels* et *Move Members* sur votre serveur, et activez l’intent `voice_states` (déjà dans le code).
- **BDD non persistée** : volume non monté sur `/data` **OU** `DB_PATH` non défini → corrigez et redeploy.
