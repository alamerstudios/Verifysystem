"""Admin-Commands: Bot-Profil (Server-Avatar) je Server einstellen."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

import utils

log = logging.getLogger("verifybot.admin")

SERVER_ICON = "server_icon"
OWNER_AVATAR = "owner_avatar"
DEFAULT_ICON = "default_icon"


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="admin_set_bot_profile",
        description="Profilbild des Bots auf diesem Server ändern.",
    )
    @app_commands.guild_only()
    @app_commands.describe(
        auswahl="Welches Bild soll der Bot auf diesem Server benutzen?",
        nickname="Optional: Anzeigename des Bots auf diesem Server",
    )
    @app_commands.choices(
        auswahl=[
            app_commands.Choice(name="1 · Server Icon", value=SERVER_ICON),
            app_commands.Choice(name="2 · Server Owner Profilbild", value=OWNER_AVATAR),
            app_commands.Choice(name="3 · Standard Icon (normales Bot-Icon)", value=DEFAULT_ICON),
        ]
    )
    async def set_bot_profile(
        self,
        interaction: discord.Interaction,
        auswahl: app_commands.Choice[str],
        nickname: str | None = None,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            await utils.safe_respond(interaction, f"{utils.NO} Nur auf einem Server nutzbar.")
            return
        if not utils.is_setup_allowed(interaction.user, guild):
            await utils.safe_respond(
                interaction,
                f"{utils.NO} Dafür brauchst du **Administrator**-Rechte, "
                "musst der **Server-Owner** oder der **Bot-Owner** sein.",
            )
            return

        await interaction.response.defer(ephemeral=True)

        me = guild.me
        if me is None and self.bot.user is not None:
            try:
                me = await guild.fetch_member(self.bot.user.id)
            except discord.HTTPException:
                me = None
        if me is None:
            await utils.safe_respond(interaction, f"{utils.NO} Ich finde mich selbst nicht auf diesem Server.")
            return

        # --- Bild besorgen ---
        avatar_bytes: bytes | None = None
        quelle = ""

        if auswahl.value == SERVER_ICON:
            if guild.icon is None:
                await utils.safe_respond(
                    interaction,
                    f"{utils.NO} Dieser Server hat **kein Icon**. "
                    "Lade zuerst eins in den Servereinstellungen hoch.",
                )
                return
            try:
                avatar_bytes = await guild.icon.read()
            except discord.HTTPException:
                await utils.safe_respond(interaction, f"{utils.NO} Server-Icon konnte nicht geladen werden.")
                return
            quelle = f"Server-Icon von **{guild.name}**"

        elif auswahl.value == OWNER_AVATAR:
            owner = guild.owner
            if owner is None:
                try:
                    owner = await guild.fetch_member(guild.owner_id)  # type: ignore[arg-type]
                except discord.HTTPException:
                    owner = None
            if owner is None:
                await utils.safe_respond(interaction, f"{utils.NO} Der Server-Owner konnte nicht geladen werden.")
                return
            try:
                avatar_bytes = await owner.display_avatar.read()
            except discord.HTTPException:
                await utils.safe_respond(interaction, f"{utils.NO} Profilbild des Owners konnte nicht geladen werden.")
                return
            quelle = f"Profilbild von **{owner.display_name}** (Server-Owner)"

        else:  # DEFAULT_ICON
            avatar_bytes = None
            quelle = "das **normale Bot-Icon**"

        # --- Setzen ---
        payload: dict = {"avatar": avatar_bytes}
        if nickname is not None:
            payload["nick"] = nickname.strip() or None

        try:
            await me.edit(**payload, reason=f"Bot-Profil geändert von {interaction.user}")
        except discord.Forbidden:
            await utils.safe_respond(
                interaction,
                f"{utils.NO} Discord hat das abgelehnt. Mögliche Gründe:\n"
                "• mir fehlt `Nickname ändern` bzw. `Nicknames verwalten`\n"
                "• server-spezifische App-Avatare sind für diesen Bot nicht verfügbar",
            )
            return
        except discord.HTTPException as exc:
            log.exception("Bot-Profil konnte nicht geändert werden.")
            await utils.safe_respond(interaction, f"{utils.NO} Fehler: `{exc.text or exc}`")
            return

        embed = discord.Embed(
            title="✅ Bot-Profil aktualisiert",
            description=f"Ich benutze auf **{guild.name}** ab jetzt {quelle}.",
            color=discord.Color.green(),
        )
        if avatar_bytes is None:
            embed.add_field(name="Avatar", value="zurückgesetzt (globales Bot-Icon)", inline=True)
        if nickname is not None:
            embed.add_field(name="Anzeigename", value=nickname or "*zurückgesetzt*", inline=True)
        embed.set_footer(text="Gilt nur für diesen Server – andere Server bleiben unverändert.")
        await utils.safe_respond(interaction, embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))
