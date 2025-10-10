# main.py
from pathlib import Path  # ← ajout
from app.config import load_settings
from app.bot import create_bot

def main():
    # 🔎 Check de présence des GIFs (affiché dans les logs Railway)
    assets_dir = Path(__file__).parents[0] / "app" / "assets" / "arena_gifs"
    try:
        files = list(assets_dir.glob("*"))
        print(f"[startup] Arena GIFs dir: {assets_dir} — exists={assets_dir.exists()} — count={len(files)}")
        for p in files[:10]:
            print(f"[startup]  - {p.name} ({p.stat().st_size} bytes)")
    except Exception as e:
        print(f"[startup] GIF check error: {e}")

    settings = load_settings()
    bot = create_bot(settings)
    bot.run(settings.DISCORD_BOT_TOKEN)

if __name__ == "__main__":
    main()
