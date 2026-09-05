"""Hilfsfunktionen: Berechtigungen, Embeds, Rollenvergabe, sichere Antworten."""
from __future__ import annotations

import logging
from typing import Any, Sequence

import discord

import config
from database import as_ids

log = logging.getLogger("verifybot.utils")

BUTTON_STYLES: dict[str, discord.ButtonStyle] = {
    "blurple": discord.ButtonStyle.primary,
    "primary": discord.ButtonStyle.primary,
    "grey": discord.ButtonStyle.secondary,
    "secondary": discord.ButtonStyle.secondary,
    "green": discord.ButtonStyle.success,
    "success": discord.ButtonStyle.success,
    "red": discord.ButtonStyle.danger,
    "danger": discord.ButtonStyle.danger,
}

OK = "✅"
NO = "❌"
WARN = "⚠️"


# ------------------------------------------------------------- Berechtigungen --
def is_setup_allowed(user: discord.abc.User, guild: discord.Guild | None) -> bool:
    """Admin-Permission ODER Server-Owner ODER Bot-Owner."""
    if user.id == config.BOT_OWNER_ID:
        return True
    if guild is None:
        return False
    if guild.owner_id == user.id:
        return True
    member = guild.get_member(user.id)
    if isinstance(user, discord.Member):
        member = user
    if member is None:
        return False
    return member.guild_permissions.administrator


def is_staff(member: discord.Member, cfg: dict[str, Any]) -> bool:
    """Darf Verifizierungs-Anfragen annehmen/ablehnen."""
    if member.id == config.BOT_OWNER_ID:
        return True
    if member.guild.owner_id == member.id:
        return True
    if member.guild_permissions.administrator or member.guild_permissions.manage_guild:
        return True
    staff_roles = set(as_ids(cfg.get("staff_role_ids")))
    return any(r.id in staff_roles for r in member.roles)


# --------------------------------------------------------------------- Embeds --
def parse_color(raw: str | int | None) -> int:
    if raw is None:
        return config.DEFAULT_EMBED_COLOR
    if isinstance(raw, int):
        return raw
    text = str(raw).strip().lstrip("#").replace("0x", "")
    named = {
        "blurple": 0x5865F2, "blau": 0x3498DB, "blue": 0x3498DB,
        "gruen": 0x2ECC71, "grün": 0x2ECC71, "green": 0x2ECC71,
        "rot": 0xE74C3C, "red": 0xE74C3C, "gelb": 0xF1C40F, "yellow": 0xF1C40F,
        "orange": 0xE67E22, "lila": 0x9B59B6, "purple": 0x9B59B6,
        "schwarz": 0x2B2D31, "black": 0x2B2D31, "weiss": 0xFFFFFF, "white": 0xFFFFFF,
    }
    if text.lower() in named:
        return named[text.lower()]
    try:
        return int(text, 16)
    except ValueError:
        return config.DEFAULT_EMBED_COLOR


def build_panel_embed(cfg: dict[str, Any], guild: discord.Guild | None = None) -> discord.Embed:
    embed = discord.Embed(
        title=(cfg.get("embed_title") or config.DEFAULT_EMBED_TITLE)[:256],
        description=(cfg.get("embed_description") or config.DEFAULT_EMBED_DESCRIPTION)[:4000],
        color=discord.Color(int(cfg.get("embed_color") or config.DEFAULT_EMBED_COLOR)),
    )
    if cfg.get("embed_footer"):
        embed.set_footer(text=str(cfg["embed_footer"])[:2048])
    if cfg.get("embed_image_url"):
        embed.set_image(url=cfg["embed_image_url"])
    if cfg.get("embed_thumbnail_url"):
        embed.set_thumbnail(url=cfg["embed_thumbnail_url"])
    elif guild is not None and guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    return embed


def button_style_of(cfg: dict[str, Any]) -> discord.ButtonStyle:
    return BUTTON_STYLES.get(
        str(cfg.get("button_style") or config.DEFAULT_BUTTON_STYLE).lower(),
        discord.ButtonStyle.success,
    )


def clean_emoji(raw: str | None) -> str | None:
    if not raw:
        return None
    raw = raw.strip()
    if raw.lower() in {"none", "kein", "keins", "-"}:
        return None
    return raw or None


def mention_roles(guild: discord.Guild | None, ids: Sequence[int] | None) -> str:
    ids = as_ids(ids)
    if not ids:
        return "*(keine)*"
    out = []
    for rid in ids:
        role = guild.get_role(rid) if guild else None
        out.append(role.mention if role else f"`{rid}` *(gelöscht)*")
    return ", ".join(out)


PERM_NAMES = {
    "view_channel": "Kanal ansehen",
    "send_messages": "Nachrichten senden",
    "embed_links": "Links einbetten",
    "read_message_history": "Verlauf lesen",
}


def channel_problem(
    guild: discord.Guild | None,
    channel_id: int | None,
    label: str,
    needed: tuple[str, ...] = ("view_channel", "send_messages", "embed_links"),
) -> str | None:
    """Prüft, ob der Bot im Kanal alles darf. Gibt eine Warnung oder None zurück."""
    if guild is None or not channel_id:
        return None
    channel = guild.get_channel(int(channel_id))
    if channel is None:
        return f"{label}: Kanal `{channel_id}` existiert nicht mehr."
    me = guild.me
    if me is None:
        return None
    try:
        perms = channel.permissions_for(me)
    except Exception:  # noqa: BLE001
        return None
    missing = [PERM_NAMES[p] for p in needed if not getattr(perms, p, True)]
    if missing:
        return f"{label} ({channel.mention}): mir fehlt " + ", ".join(f"`{m}`" for m in missing)
    return None


def mention_channel(guild: discord.Guild | None, channel_id: int | None) -> str:
    if not channel_id:
        return "*(nicht gesetzt)*"
    channel = guild.get_channel(int(channel_id)) if guild else None
    return channel.mention if channel else f"`{channel_id}` *(gelöscht)*"


# ------------------------------------------------------------------- Rollen ----
async def apply_verification_roles(
    member: discord.Member, cfg: dict[str, Any], reason: str = "Verifizierung"
) -> tuple[list[discord.Role], list[discord.Role], list[str]]:
    """Vergibt/entfernt die konfigurierten Rollen. Gibt (added, removed, errors) zurueck."""
    guild = member.guild
    me = guild.me
    added: list[discord.Role] = []
    removed: list[discord.Role] = []
    errors: list[str] = []

    def usable(role: discord.Role | None) -> bool:
        if role is None:
            return False
        if role.is_default() or role.managed:
            return False
        return me is not None and role < me.top_role

    to_add = [guild.get_role(r) for r in as_ids(cfg.get("add_role_ids"))]
    to_remove = [guild.get_role(r) for r in as_ids(cfg.get("remove_role_ids"))]

    add_roles = [r for r in to_add if usable(r) and r not in member.roles]
    remove_roles = [r for r in to_remove if usable(r) and r in member.roles]

    for role in [r for r in to_add if r is not None and not usable(r)]:
        errors.append(f"Kann `{role.name}` nicht vergeben (Rollenhierarchie/Bot-Rechte).")
    for role in [r for r in to_remove if r is not None and not usable(r)]:
        errors.append(f"Kann `{role.name}` nicht entfernen (Rollenhierarchie/Bot-Rechte).")

    try:
        if add_roles:
            await member.add_roles(*add_roles, reason=reason)
            added = add_roles
    except discord.Forbidden:
        errors.append("Keine Berechtigung zum Vergeben von Rollen (`Rollen verwalten` fehlt).")
    except discord.HTTPException as exc:
        errors.append(f"Fehler beim Vergeben von Rollen: {exc}")

    try:
        if remove_roles:
            await member.remove_roles(*remove_roles, reason=reason)
            removed = remove_roles
    except discord.Forbidden:
        errors.append("Keine Berechtigung zum Entfernen von Rollen (`Rollen verwalten` fehlt).")
    except discord.HTTPException as exc:
        errors.append(f"Fehler beim Entfernen von Rollen: {exc}")

    return added, removed, errors


# --------------------------------------------------- sichere Interaktionen -----
async def safe_respond(
    interaction: discord.Interaction,
    content: str | None = None,
    *,
    embed: discord.Embed | None = None,
    view: discord.ui.View | None = None,
    ephemeral: bool = True,
) -> None:
    """Antwortet garantiert - egal ob die Interaction schon beantwortet wurde."""
    kwargs: dict[str, Any] = {}
    if content is not None:
        kwargs["content"] = content
    if embed is not None:
        kwargs["embed"] = embed
    if view is not None:
        kwargs["view"] = view
    try:
        if interaction.response.is_done():
            await interaction.followup.send(ephemeral=ephemeral, **kwargs)
        else:
            await interaction.response.send_message(ephemeral=ephemeral, **kwargs)
    except discord.HTTPException:
        log.exception("Antwort auf Interaction fehlgeschlagen.")


def truncate(text: str, limit: int = 1024) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1] + "…"
