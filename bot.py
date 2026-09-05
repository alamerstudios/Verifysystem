"""Verify-Bot – Einstiegspunkt.

Start (lokal & Render):  python bot.py
"""
from __future__ import annotations

import logging
import sys
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

import config
import utils
from database import Database
from keepalive import start_keepalive
from views.verify import AcceptButton, DenyButton, VerifyPanelView

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)-8s %(name)s: %(message)s",
    datefmt="%d.%m.%Y %H:%M:%S",
)
logging.getLogger("discord.http").setLevel(logging.WARNING)
logging.getLogger("discord.gateway").setLevel(logging.WARNING)
log = logging.getLogger("verifybot")

INTENTS = discord.Intents.default()
INTENTS.members = True          # nötig für Rollen & Auto-Rollen (im Dev-Portal aktivieren!)
INTENTS.message_content = False


class VerifyBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=INTENTS,
            help_command=None,
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=True, users=True),
        )
        self.db = Database(config.DATABASE_URL or "")
        # guild_id -> {"config": ..., "fields": [...], "checkboxes": [...]}
        self.cache: dict[int, dict[str, Any]] = {}
        self._keepalive = None

    # --------------------------------------------------------------- cache --
    async def load_guild(self, guild_id: int) -> dict[str, Any]:
        cfg = await self.db.get_config(guild_id)
        data = {
            "config": cfg,
            "fields": await self.db.get_form_fields(guild_id),
            "checkboxes": await self.db.get_checkboxes(guild_id),
        }
        self.cache[guild_id] = data
        return data

    async def get_cfg(self, guild_id: int) -> dict[str, Any]:
        cached = self.cache.get(guild_id)
        if cached is not None:
            return cached["config"]
        return (await self.load_guild(guild_id))["config"]

    async def update_cfg(self, guild_id: int, **values: Any) -> dict[str, Any]:
        cfg = await self.db.update_config(guild_id, **values)
        data = self.cache.setdefault(guild_id, {"fields": [], "checkboxes": []})
        data["config"] = cfg
        return cfg

    # ------------------------------------------------------------ lifecycle --
    async def setup_hook(self) -> None:
        self._keepalive = await start_keepalive()

        await self.db.connect()

        # Persistente Komponenten registrieren -> Buttons funktionieren
        # auch nach einem Neustart / Deploy weiter (kein "Interaction failed").
        self.add_view(VerifyPanelView(self))
        self.add_dynamic_items(AcceptButton, DenyButton)

        await self.load_extension("cogs.setup_cog")
        await self.load_extension("cogs.events")

        if config.DEV_GUILD_ID:
            guild = discord.Object(id=config.DEV_GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            log.info("Commands für Test-Server %s synchronisiert.", config.DEV_GUILD_ID)
        synced = await self.tree.sync()
        log.info("%s globale Slash-Commands synchronisiert.", len(synced))

        self.tree.on_error = self.on_app_command_error  # type: ignore[assignment]

    async def on_ready(self) -> None:
        log.info("Eingeloggt als %s (ID: %s)", self.user, self.user.id if self.user else "?")
        log.info("Aktiv auf %s Server(n).", len(self.guilds))

        # Konfigurationen vorladen -> der Verify-Button antwortet sofort.
        for guild in self.guilds:
            try:
                await self.load_guild(guild.id)
            except Exception:  # noqa: BLE001
                log.exception("Cache für Guild %s fehlgeschlagen.", guild.id)

        await self.change_presence(
            activity=discord.Activity(type=discord.ActivityType.watching, name="/setup")
        )

    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self.load_guild(guild.id)

    async def close(self) -> None:
        if self._keepalive is not None:
            self._keepalive.close()
        await self.db.close()
        await super().close()

    # ---------------------------------------------------------------- errors --
    async def on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.CommandOnCooldown):
            await utils.safe_respond(
                interaction, f"{utils.WARN} Bitte warte {error.retry_after:.0f} Sekunden."
            )
            return
        if isinstance(error, app_commands.MissingPermissions):
            await utils.safe_respond(interaction, f"{utils.NO} Dir fehlen die nötigen Rechte.")
            return
        log.exception("Fehler in App-Command", exc_info=error)
        await utils.safe_respond(
            interaction, f"{utils.NO} Es ist ein Fehler aufgetreten. Bitte versuche es erneut."
        )

    async def on_error(self, event_method: str, /, *args: Any, **kwargs: Any) -> None:
        log.exception("Unbehandelter Fehler im Event %s", event_method)


def main() -> None:
    if not config.TOKEN:
        log.critical("DISCORD_TOKEN fehlt! Bitte als Umgebungsvariable setzen.")
        sys.exit(1)
    if not config.DATABASE_URL:
        log.critical("SUPABASE_DB_URL (oder DATABASE_URL) fehlt! Bitte setzen.")
        sys.exit(1)

    bot = VerifyBot()
    try:
        bot.run(config.TOKEN, log_handler=None)
    except discord.LoginFailure:
        log.critical("Ungültiger Bot-Token.")
        sys.exit(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
