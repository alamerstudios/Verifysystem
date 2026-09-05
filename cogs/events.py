"""Events: Auto-Rollen beim Beitritt, Aufräumen, Fehlerbehandlung."""
from __future__ import annotations

import logging

import discord
from discord.ext import commands

from database import as_ids

log = logging.getLogger("verifybot.events")


class EventsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return
        try:
            cfg = await self.bot.get_cfg(member.guild.id)
        except Exception:  # noqa: BLE001
            log.exception("Konfiguration beim Join konnte nicht geladen werden.")
            return

        role_ids = as_ids(cfg.get("autorole_ids"))
        if not role_ids:
            return

        me = member.guild.me
        roles = [
            r for r in (member.guild.get_role(rid) for rid in role_ids)
            if r is not None and not r.managed and not r.is_default()
            and me is not None and r < me.top_role
        ]
        if not roles:
            return
        try:
            await member.add_roles(*roles, reason="Auto-Rolle beim Serverbeitritt")
        except discord.HTTPException:
            log.warning("Auto-Rollen für %s in %s fehlgeschlagen.", member, member.guild)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        try:
            cfg = await self.bot.get_cfg(channel.guild.id)
        except Exception:  # noqa: BLE001
            return
        updates = {}
        if cfg.get("panel_channel_id") == channel.id:
            updates["panel_channel_id"] = None
            updates["panel_message_id"] = None
            updates["panel_message_channel_id"] = None
        if cfg.get("requests_channel_id") == channel.id:
            updates["requests_channel_id"] = None
        if updates:
            await self.bot.update_cfg(channel.guild.id, **updates)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        self.bot.cache.pop(guild.id, None)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EventsCog(bot))
