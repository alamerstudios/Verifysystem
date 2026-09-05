"""Slash-Commands rund um das Verify-System."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

import config
import utils
from views.setup_wizard import SetupPanelView, SetupWizard
from views.verify import send_verify_panel

log = logging.getLogger("verifybot.commands")


class SetupCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------ /setup
    @app_commands.command(
        name="setup",
        description="Verify-System einrichten (Kanäle, Rollen, Embed, Formular & Checkboxen).",
    )
    @app_commands.guild_only()
    async def setup(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await utils.safe_respond(interaction, f"{utils.NO} Nur auf einem Server nutzbar.")
            return
        if not utils.is_setup_allowed(interaction.user, interaction.guild):
            await utils.safe_respond(
                interaction,
                f"{utils.NO} Dafür brauchst du **Administrator**-Rechte, "
                "musst der **Server-Owner** oder der **Bot-Owner** sein.",
            )
            return

        cfg = await self.bot.get_cfg(interaction.guild.id)
        if cfg.get("setup_completed"):
            # Setup ist schon durch -> direkt das Verwaltungs-Menü zum Bearbeiten
            view = SetupPanelView(self.bot, interaction.guild, interaction.user.id)
        else:
            view = SetupWizard(self.bot, interaction.guild, interaction.user.id, step=0)
        await view.start(interaction)

    # ------------------------------------------------------------ /verify-panel
    @app_commands.command(
        name="verify-panel",
        description="Das Verify-Embed (neu) in den eingestellten Kanal senden.",
    )
    @app_commands.guild_only()
    @app_commands.describe(channel="Optional: anderer Kanal als der eingestellte")
    async def verify_panel(
        self, interaction: discord.Interaction, channel: discord.TextChannel | None = None
    ) -> None:
        guild = interaction.guild
        if guild is None:
            return
        if not utils.is_setup_allowed(interaction.user, guild):
            await utils.safe_respond(interaction, f"{utils.NO} Dazu hast du keine Berechtigung.")
            return

        await interaction.response.defer(ephemeral=True)
        cfg = await self.bot.get_cfg(guild.id)
        _message, info = await send_verify_panel(self.bot, guild, cfg, channel)
        await utils.safe_respond(interaction, info)

    # ----------------------------------------------------------- /verify-config
    @app_commands.command(
        name="verify-config", description="Aktuelle Einstellungen des Verify-Systems anzeigen."
    )
    @app_commands.guild_only()
    async def verify_config(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return
        if not utils.is_setup_allowed(interaction.user, guild):
            await utils.safe_respond(interaction, f"{utils.NO} Dazu hast du keine Berechtigung.")
            return

        await interaction.response.defer(ephemeral=True)
        cfg = await self.bot.get_cfg(guild.id)
        fields = await self.bot.db.get_form_fields(guild.id)
        boxes = await self.bot.db.get_checkboxes(guild.id)

        embed = discord.Embed(title="⚙️ Verify-Konfiguration", color=discord.Color.blurple())
        embed.add_field(name="Verify-Kanal",
                        value=utils.mention_channel(guild, cfg.get("panel_channel_id")), inline=True)
        embed.add_field(name="Anfragen-Kanal",
                        value=utils.mention_channel(guild, cfg.get("requests_channel_id")), inline=True)
        embed.add_field(name="Team-Rollen",
                        value=utils.mention_roles(guild, cfg.get("staff_role_ids")), inline=False)
        embed.add_field(name="Rollen +",
                        value=utils.mention_roles(guild, cfg.get("add_role_ids")), inline=True)
        embed.add_field(name="Rollen −",
                        value=utils.mention_roles(guild, cfg.get("remove_role_ids")), inline=True)
        embed.add_field(name="Auto-Rollen (Join)",
                        value=utils.mention_roles(guild, cfg.get("autorole_ids")), inline=False)
        embed.add_field(
            name="Bewerbung",
            value=(
                (("\n".join(f"📝 {f['label']}" for f in fields)) or "*keine Fragen*")
                + "\n"
                + (("\n".join(f"☑️ {b['label']}" for b in boxes)) or "*keine Checkboxen*")
            )[:1024],
            inline=False,
        )
        embed.add_field(name="Auto-Annahme", value="an" if cfg.get("auto_approve") else "aus", inline=True)
        embed.add_field(name="DM an Nutzer", value="an" if cfg.get("dm_user", True) else "aus", inline=True)
        embed.add_field(name="Setup abgeschlossen",
                        value="ja" if cfg.get("setup_completed") else "nein", inline=True)
        await utils.safe_respond(interaction, embed=embed)

    # ------------------------------------------------------------ /verify-reset
    @app_commands.command(
        name="verify-reset", description="Alle Verify-Einstellungen dieses Servers zurücksetzen."
    )
    @app_commands.guild_only()
    async def verify_reset(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return
        if not utils.is_setup_allowed(interaction.user, guild):
            await utils.safe_respond(interaction, f"{utils.NO} Dazu hast du keine Berechtigung.")
            return

        view = ConfirmResetView(self.bot, guild.id, interaction.user.id)
        await interaction.response.send_message(
            f"{utils.WARN} Wirklich **alle** Verify-Einstellungen (inkl. Formular & "
            "Checkboxen) löschen?",
            view=view, ephemeral=True,
        )

    # ----------------------------------------------------------------- /verify
    @app_commands.command(
        name="verify", description="Einen Nutzer manuell verifizieren (Team)."
    )
    @app_commands.guild_only()
    @app_commands.describe(member="Der Nutzer, der verifiziert werden soll")
    async def verify_member(
        self, interaction: discord.Interaction, member: discord.Member
    ) -> None:
        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            return
        await interaction.response.defer(ephemeral=True)
        cfg = await self.bot.get_cfg(guild.id)
        if not utils.is_staff(interaction.user, cfg):
            await utils.safe_respond(interaction, f"{utils.NO} Dazu hast du keine Berechtigung.")
            return

        added, removed, errors = await utils.apply_verification_roles(
            member, cfg, reason=f"Manuell verifiziert von {interaction.user}"
        )
        request_id = await self.bot.db.create_request(guild.id, member.id, [], [])
        await self.bot.db.close_request(request_id, "approved", interaction.user.id,
                                        "Manuell verifiziert")
        text = f"{utils.OK} {member.mention} wurde verifiziert."
        if added:
            text += f"\n➕ {', '.join(r.mention for r in added)}"
        if removed:
            text += f"\n➖ {', '.join(r.mention for r in removed)}"
        if errors:
            text += f"\n{utils.WARN} " + "\n".join(errors)
        await utils.safe_respond(interaction, text)

    # ------------------------------------------------------------------- /sync
    @app_commands.command(name="sync", description="Slash-Commands neu synchronisieren (Bot-Owner).")
    async def sync(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != config.BOT_OWNER_ID:
            await utils.safe_respond(interaction, f"{utils.NO} Nur der Bot-Owner.")
            return
        await interaction.response.defer(ephemeral=True)
        synced = await self.bot.tree.sync()
        await utils.safe_respond(interaction, f"{utils.OK} {len(synced)} Commands synchronisiert.")


class ConfirmResetView(discord.ui.View):
    def __init__(self, bot, guild_id: int, author_id: int):
        super().__init__(timeout=120)
        self.bot = bot
        self.guild_id = guild_id
        self.author_id = author_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id

    @discord.ui.button(label="Ja, zurücksetzen", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.bot.db.reset_guild(self.guild_id)
        self.bot.cache.pop(self.guild_id, None)
        await interaction.response.edit_message(
            content=f"{utils.OK} Zurückgesetzt. Führe `/setup` aus, um neu zu starten.", view=None
        )

    @discord.ui.button(label="Abbrechen", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(content="Abgebrochen.", view=None)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SetupCog(bot))
