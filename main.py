# main.py
from app.config import load_settings
from app.bot import create_bot

def main():
    settings = load_settings()
    bot = create_bot(settings)
    bot.run(settings.DISCORD_BOT_TOKEN)

if __name__ == "__main__":
    main()
