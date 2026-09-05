"""Zentrale Konfiguration / Konstanten des Verify-Bots."""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip().strip('"').strip("'")
    return value or None


# ---------------------------------------------------------------- Discord ----
TOKEN: str | None = _clean(os.getenv("DISCORD_TOKEN") or os.getenv("BOT_TOKEN"))

# Der Bot-Owner darf /setup IMMER ausfuehren (auch ohne Adminrechte).
BOT_OWNER_ID: int = int(_clean(os.getenv("BOT_OWNER_ID")) or 1313187996790427688)

_dev_guild = _clean(os.getenv("DEV_GUILD_ID"))
DEV_GUILD_ID: int | None = int(_dev_guild) if _dev_guild and _dev_guild.isdigit() else None

# --------------------------------------------------------------- Database ----
DATABASE_URL: str | None = _clean(
    os.getenv("SUPABASE_DB_URL")
    or os.getenv("DATABASE_URL")
    or os.getenv("POSTGRES_URL")
)

# ---------------------------------------------------------------- Limits -----
MAX_FORM_FIELDS = 5      # Discord-Limit: 5 Eingabefelder pro Modal
MAX_CHECKBOXES = 20      # 4 Button-Reihen a 5 Buttons (5. Reihe = Absenden)

# --------------------------------------------------------------- Defaults ----
DEFAULT_EMBED_TITLE = "🔐 Verifizierung"
DEFAULT_EMBED_DESCRIPTION = (
    "Willkommen auf dem Server!\n\n"
    "Um Zugriff auf alle Kanäle zu erhalten, klicke auf den Button **Verifizieren** "
    "und fülle das kurze Formular aus.\n\n"
    "Unser Team prüft deine Anfrage anschließend so schnell wie möglich."
)
DEFAULT_EMBED_COLOR = 0x5865F2
DEFAULT_BUTTON_LABEL = "Verifizieren"
DEFAULT_BUTTON_EMOJI = "✅"
DEFAULT_BUTTON_STYLE = "success"

# --------------------------------------------------------------- Custom IDs --
VERIFY_BUTTON_ID = "verifysystem:panel:verify"
