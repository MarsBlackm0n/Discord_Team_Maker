# ⚙️ SETUP technique (local & Discord)

Ce guide couvre la configuration **locale**, la **création du bot** côté Discord et les **permissions** nécessaires.

---

## 1) Local : cloner & installer
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

Créez un fichier `.env` **à la racine** (pas commité) :
```env
DISCORD_BOT_TOKEN=xxxxx
OWNER_ID=123456789012345678     # (optionnel) votre user id
GUILD_ID=123456789012345678     # pour sync slash immédiate sur VOTRE serveur
RIOT_API_KEY=                   # (optionnel) laissez vide si vous n'en avez pas
DB_PATH=skills.db               # en local, fichier à côté du script (peut être omis)
RESTART_MODE=self               # en local, redémarrage "self" possible
```

Lancez le bot :
```bash
python main.py
```
- Arrêt : **Ctrl+C** dans le terminal.
- Test rapide : `/whoami`, `/setskill`, `/ranks`…

---

## 2) Créer l’application & le bot sur le **Developer Portal**
1. **New Application** → nommez-la.
2. Onglet **Bot** → **Add Bot**.
3. **Privileged Gateway Intents** : activez **Server Members Intent** (recommandé).  
   > *Message Content* n’est pas nécessaire pour ce bot.
4. **Reset Token** si besoin et copiez-le → c’est `DISCORD_BOT_TOKEN` dans votre `.env`.

---

## 3) Inviter le bot sur votre serveur
Dans **OAuth2 → URL Generator** :
- **Scopes** : `bot`, `applications.commands`
- **Bot Permissions** (minimum conseillé) :
  - *Use Application Commands*
  - *Manage Channels*
  - *Move Members*
  - *(optionnel)* *Send Messages*, *Embed Links* (pour les réponses classiques)
Générez l’URL, ouvrez-la et choisissez votre **serveur**.

> Après invitation, les commandes peuvent mettre un peu de temps à apparaître **si vous ne faites pas une sync ciblée**. Mettez votre `GUILD_ID` et/ou utilisez `/resync`.

---

## 4) Riot API (optionnel)
- Sans clé, le bot fonctionne en **mode offline** : `/setrank` (tier/division/LP) ou `/setskill`.
- Avec `RIOT_API_KEY`, la commande `/linklol` importe le rang **SoloQ** et calcule un rating.
- Régions supportées (paramètre `region`) : `EUW, EUNE, NA, KR, BR, JP, LAN, LAS, OCE, TR, RU`.

---

## 5) Vérifications utiles
- Le log au démarrage affiche : `slash prêts (...)` et le chemin DB.
- `/resync` (admin) force la resynchronisation des commandes.
- `/whoami` renvoie votre User ID (à mettre dans `OWNER_ID` si vous voulez les commandes admin).

Pour l’**hébergement** (Railway + volume persistant), suivez **DEPLOY_RAILWAY.md**.
