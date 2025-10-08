# 🚂 Déployer sur Railway (Free)

Ce dépôt est prêt pour **Railway Free** (trial $5 puis $1/mo de crédits). Plan “Free” : jusqu’à **0.5 GB RAM / 1 vCPU** par service + **0.5 GB de volume**. (Voir Pricing)

## Étapes

1) **Fork** ou clonez ce repo sur GitHub (sans .env réel).
2) Sur Railway: **New Project → Deploy from GitHub** (choisissez ce repo).
3) Variables (Service → Variables) :
   - DISCORD_BOT_TOKEN
   - OWNER_ID (optionnel)
   - RESTART_MODE=manager
   - RIOT_API_KEY (optionnel)
4) **Volume** (Service → Disks/Volumes) : ajoutez un volume monté sur `/data` puis set `DB_PATH=/data/skills.db` dans Variables.
5) Deploy → le bot reste en ligne; utilisez `/restart` pour recycler.

## Notes
- Les variables Railway sont injectées au build & au runtime.
- Avec `restartPolicyType=ALWAYS`, quitter (`/restart`) déclenche un redémarrage par Railway.
- Sans volume, `skills.db` est éphémère (perdu lors des rebuilds).
