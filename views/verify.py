"""Alles rund um den Verifizieren-Button, Formular, Checkboxen und die Team-Prüfung."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import discord

import config
import utils
from database import as_ids

log = logging.getLogger("verifybot.verify")

CHECK_ON = "☑️"
CHECK_OFF = "⬜"


# ============================================================== Formular ======
async def fetch_checkboxes(bot, guild_id: int, fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Holt die Checkboxen notfalls frisch aus der DB (gegen veralteten Cache)."""
    if fallback:
        return fallback
    try:
        return await asyncio.wait_for(bot.db.get_checkboxes(guild_id), timeout=1.5)
    except Exception:  # noqa: BLE001 - Timeout/DB-Fehler -> mit dem arbeiten, was da ist
        log.warning("Checkboxen konnten nicht nachgeladen werden (Guild %s).", guild_id)
        return fallback


class VerifyFormModal(discord.ui.Modal):
    """Formular mit Textfeldern UND echten Discord-Checkboxen (Modal-Komponenten)."""

    MAX_COMPONENTS = 5   # Discord-Limit fuer Komponenten in einem Modal

    def __init__(self, bot, fields: list[dict[str, Any]], checkboxes: list[dict[str, Any]]):
        super().__init__(title="Verifizierung", timeout=900)
        self.bot = bot
        self.inputs: list[tuple[dict[str, Any], discord.ui.TextInput]] = []
        # (Gruppe, zugehoerige Checkbox-Datensaetze)
        self.groups: list[tuple[Any, list[dict[str, Any]]]] = []

        required = [c for c in checkboxes if c.get("required", True)][:10]
        optional = [c for c in checkboxes if not c.get("required", True)][:10]

        budget = self.MAX_COMPONENTS - (1 if required else 0) - (1 if optional else 0)
        self.fields = list(fields)[: max(0, budget)]

        for field in self.fields:
            text_input = discord.ui.TextInput(
                placeholder=(field.get("placeholder") or None),
                style=(discord.TextStyle.paragraph
                       if str(field.get("style")) == "paragraph"
                       else discord.TextStyle.short),
                required=bool(field.get("required", True)),
                max_length=int(field["max_length"]) if field.get("max_length") else None,
            )
            self.inputs.append((field, text_input))
            self.add_item(discord.ui.Label(
                text=utils.truncate(str(field["label"]), 45), component=text_input
            ))

        if required:
            group = discord.ui.CheckboxGroup(
                options=[
                    discord.CheckboxGroupOption(
                        label=utils.truncate(str(c["label"]), 100),
                        value=str(c["id"]),
                        description=utils.truncate(str(c["description"]), 100) if c.get("description") else None,
                    )
                    for c in required
                ],
                min_values=len(required),      # alle Pflicht-Punkte muessen angehakt sein
                max_values=len(required),
                required=True,
            )
            self.groups.append((group, required))
            self.add_item(discord.ui.Label(
                text="Pflicht – bitte alles bestätigen",
                description="Ohne diese Häkchen kannst du das Formular nicht absenden.",
                component=group,
            ))

        if optional:
            group = discord.ui.CheckboxGroup(
                options=[
                    discord.CheckboxGroupOption(
                        label=utils.truncate(str(c["label"]), 100),
                        value=str(c["id"]),
                        description=utils.truncate(str(c["description"]), 100) if c.get("description") else None,
                    )
                    for c in optional
                ],
                min_values=0,
                max_values=len(optional),
                required=False,
            )
            self.groups.append((group, optional))
            self.add_item(discord.ui.Label(
                text="Optional", description="Freiwillig – kannst du auch leer lassen.",
                component=group,
            ))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        answers = [
            {"label": str(f["label"]), "value": (i.value or "").strip()}
            for f, i in self.inputs
        ]
        checks: list[dict[str, Any]] = []
        for group, boxes in self.groups:
            selected = set(getattr(group, "values", []) or [])
            for box in boxes:
                checks.append({
                    "label": str(box["label"]),
                    "checked": str(box["id"]) in selected,
                    "required": bool(box.get("required", True)),
                })

        missing = [c["label"] for c in checks if c["required"] and not c["checked"]]
        if missing:
            await interaction.response.send_message(
                f"{utils.WARN} Du musst noch bestätigen:\n"
                + "\n".join(f"• {m}" for m in missing)
                + "\n\nKlicke einfach erneut auf **Verifizieren**.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        await submit_request(self.bot, interaction, answers, checks)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("Fehler im Verify-Formular", exc_info=error)
        await utils.safe_respond(
            interaction,
            f"{utils.NO} Beim Absenden ist ein Fehler aufgetreten. Bitte versuche es erneut.",
        )


# ======================================================= Anfrage einreichen ===
def build_request_embed(
    guild: discord.Guild,
    user: discord.abc.User,
    request_id: int,
    answers: list[dict[str, Any]],
    checks: list[dict[str, Any]],
) -> discord.Embed:
    embed = discord.Embed(
        title=f"🕓 Verifizierungs-Anfrage #{request_id}",
        color=discord.Color.orange(),
        timestamp=discord.utils.utcnow(),
    )
    embed.set_author(name=f"{user} ({user.id})", icon_url=user.display_avatar.url)
    embed.add_field(name="Nutzer", value=f"{user.mention}\n`{user.id}`", inline=True)

    created = discord.utils.format_dt(user.created_at, "R")
    joined = ""
    member = guild.get_member(user.id)
    if member and member.joined_at:
        joined = f"\nBeigetreten: {discord.utils.format_dt(member.joined_at, 'R')}"
    embed.add_field(name="Account", value=f"Erstellt: {created}{joined}", inline=True)

    for answer in answers:
        embed.add_field(
            name=utils.truncate(answer["label"], 256),
            value=utils.truncate(answer["value"] or "*(leer)*", 1024),
            inline=False,
        )

    if checks:
        embed.add_field(
            name="Checkboxen",
            value=utils.truncate(
                "\n".join(
                    f"{CHECK_ON if c['checked'] else CHECK_OFF} {c['label']}" for c in checks
                ),
                1024,
            ),
            inline=False,
        )

    embed.set_thumbnail(url=user.display_avatar.url)
    embed.set_footer(text=f"Anfrage-ID: {request_id} • Status: offen")
    return embed


async def submit_request(
    bot,
    interaction: discord.Interaction,
    answers: list[dict[str, Any]],
    checks: list[dict[str, Any]],
    edit_original: bool = False,
) -> None:
    """Speichert die Anfrage und postet sie im Anfragen-Channel."""
    guild = interaction.guild
    user = interaction.user
    if guild is None or not isinstance(user, discord.Member):
        await utils.safe_respond(interaction, f"{utils.NO} Das geht nur auf einem Server.")
        return

    async def reply(text: str) -> None:
        if edit_original:
            try:
                await interaction.edit_original_response(content=text, embed=None, view=None)
                return
            except discord.HTTPException:
                pass
        await utils.safe_respond(interaction, text)

    cfg = await bot.get_cfg(guild.id)

    # Bereits verifiziert? Dann keine zweite Anfrage.
    try:
        if await bot.db.is_verified(guild.id, user.id):
            await reply(
                f"{utils.OK} Du wurdest bereits verifiziert – eine zweite Anfrage "
                "ist nicht nötig."
            )
            return
    except Exception:  # noqa: BLE001
        pass

    # Doppelte offene Anfragen verhindern
    pending = await bot.db.get_pending_request(guild.id, user.id)
    if pending:
        await reply(
            f"{utils.WARN} Du hast bereits eine **offene** Anfrage (#{pending['id']}). "
            "Bitte warte, bis das Team sie geprüft hat."
        )
        return

    request_id = await bot.db.create_request(guild.id, user.id, answers, checks)

    # Auto-Annahme (falls im Setup aktiviert)
    if cfg.get("auto_approve"):
        added, removed, errors = await utils.apply_verification_roles(user, cfg)
        await bot.db.close_request(request_id, "approved", bot.user.id, "Automatisch angenommen")
        await reply(
            f"{utils.OK} Du wurdest **verifiziert**! Viel Spaß auf **{guild.name}**."
            + (f"\n{utils.WARN} " + " ".join(errors) if errors else "")
        )
        await log_result(bot, guild, cfg, user, "approved", None, None, request_id)
        return

    channel_id = cfg.get("requests_channel_id")
    channel = guild.get_channel(int(channel_id)) if channel_id else None
    if channel is None and channel_id:
        try:
            channel = await bot.fetch_channel(int(channel_id))
        except discord.HTTPException:
            channel = None
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        await reply(
            f"{utils.NO} Der Kanal für Verifizierungs-Anfragen ist nicht (mehr) konfiguriert. "
            "Bitte melde dich beim Server-Team – `/setup` muss neu ausgeführt werden."
        )
        return

    embed = build_request_embed(guild, user, request_id, answers, checks)
    view = ReviewView(request_id)

    staff_ping = ""
    staff_ids = as_ids(cfg.get("staff_role_ids"))
    if staff_ids:
        staff_ping = " ".join(f"<@&{r}>" for r in staff_ids)

    try:
        message = await channel.send(
            content=staff_ping or None,
            embed=embed,
            view=view,
            allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False),
        )
    except discord.Forbidden:
        await reply(
            f"{utils.NO} Ich darf im Anfragen-Kanal nicht schreiben. "
            "Bitte informiere das Server-Team."
        )
        return
    except discord.HTTPException:
        log.exception("Anfrage konnte nicht gesendet werden.")
        await reply(f"{utils.NO} Anfrage konnte nicht gesendet werden. Bitte später erneut versuchen.")
        return

    await bot.db.set_request_message(request_id, channel.id, message.id)
    await reply(
        f"{utils.OK} Deine Anfrage (**#{request_id}**) wurde eingereicht!\n"
        "Das Team meldet sich bei dir, sobald sie geprüft wurde."
    )


async def log_result(
    bot,
    guild: discord.Guild,
    cfg: dict[str, Any],
    user: discord.abc.User,
    status: str,
    moderator: discord.abc.User | None,
    reason: str | None,
    request_id: int,
) -> None:
    channel_id = cfg.get("log_channel_id") or cfg.get("requests_channel_id")
    if not channel_id:
        return
    channel = guild.get_channel(int(channel_id))
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        return
    if channel_id == cfg.get("requests_channel_id") and moderator is not None:
        return  # dort steht das Ergebnis schon in der bearbeiteten Nachricht
    embed = discord.Embed(
        title=f"{'✅ Angenommen' if status == 'approved' else '❌ Abgelehnt'} • #{request_id}",
        description=f"{user.mention} (`{user.id}`)",
        color=discord.Color.green() if status == "approved" else discord.Color.red(),
        timestamp=discord.utils.utcnow(),
    )
    if moderator:
        embed.add_field(name="Bearbeitet von", value=moderator.mention, inline=True)
    if reason:
        embed.add_field(name="Grund", value=utils.truncate(reason, 1024), inline=False)
    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        pass


# ================================================ Team-Prüfung (persistent) ===
async def handle_decision(
    interaction: discord.Interaction,
    request_id: int,
    approve: bool,
    reason: str | None = None,
) -> None:
    bot = interaction.client
    guild = interaction.guild
    if guild is None or not isinstance(interaction.user, discord.Member):
        await utils.safe_respond(interaction, f"{utils.NO} Nur auf einem Server möglich.")
        return

    cfg = await bot.get_cfg(guild.id)
    if not utils.is_staff(interaction.user, cfg):
        await utils.safe_respond(
            interaction, f"{utils.NO} Du darfst Verifizierungen nicht bearbeiten."
        )
        return

    if not interaction.response.is_done():
        await interaction.response.defer()

    request = await bot.db.get_request(request_id)
    if request is None:
        await utils.safe_respond(interaction, f"{utils.NO} Diese Anfrage existiert nicht mehr.")
        return
    if request["status"] != "pending":
        await utils.safe_respond(
            interaction,
            f"{utils.WARN} Diese Anfrage wurde bereits bearbeitet "
            f"(**{request['status']}**).",
        )
        return

    member = guild.get_member(int(request["user_id"]))
    if member is None:
        try:
            member = await guild.fetch_member(int(request["user_id"]))
        except discord.HTTPException:
            member = None

    errors: list[str] = []
    added: list[discord.Role] = []
    removed: list[discord.Role] = []
    if approve and member is not None:
        added, removed, errors = await utils.apply_verification_roles(
            member, cfg, reason=f"Verifizierung #{request_id} von {interaction.user}"
        )
    elif approve and member is None:
        errors.append("Nutzer ist nicht mehr auf dem Server – Rollen konnten nicht vergeben werden.")

    await bot.db.close_request(
        request_id, "approved" if approve else "denied", interaction.user.id, reason
    )

    # Nachricht aktualisieren
    message = interaction.message
    if message is None and request.get("message_id") and request.get("channel_id"):
        channel = guild.get_channel(int(request["channel_id"]))
        if isinstance(channel, (discord.TextChannel, discord.Thread)):
            try:
                message = await channel.fetch_message(int(request["message_id"]))
            except discord.HTTPException:
                message = None
    if message is not None:
        embed = message.embeds[0] if message.embeds else discord.Embed()
        embed.title = (
            f"✅ Verifizierung #{request_id} angenommen"
            if approve
            else f"❌ Verifizierung #{request_id} abgelehnt"
        )
        embed.color = discord.Color.green() if approve else discord.Color.red()
        embed.add_field(
            name="Bearbeitet von",
            value=f"{interaction.user.mention} • {discord.utils.format_dt(discord.utils.utcnow(), 'f')}",
            inline=False,
        )
        if reason:
            embed.add_field(name="Grund", value=utils.truncate(reason, 1024), inline=False)
        if added:
            embed.add_field(
                name="Rollen vergeben", value=", ".join(r.mention for r in added), inline=True
            )
        if removed:
            embed.add_field(
                name="Rollen entfernt", value=", ".join(r.mention for r in removed), inline=True
            )
        if errors:
            embed.add_field(name=f"{utils.WARN} Hinweise", value=utils.truncate("\n".join(errors)), inline=False)
        embed.set_footer(text=f"Anfrage-ID: {request_id} • Status: {'angenommen' if approve else 'abgelehnt'}")
        try:
            await message.edit(content=None, embed=embed, view=None)
        except discord.HTTPException:
            pass

    # Nutzer benachrichtigen
    if member is not None and cfg.get("dm_user", True):
        try:
            dm = discord.Embed(
                title=f"{'✅ Verifiziert' if approve else '❌ Verifizierung abgelehnt'}",
                description=(
                    f"Deine Verifizierung auf **{guild.name}** wurde "
                    f"{'angenommen. Viel Spaß!' if approve else 'abgelehnt.'}"
                ),
                color=discord.Color.green() if approve else discord.Color.red(),
            )
            if reason:
                dm.add_field(name="Grund", value=utils.truncate(reason, 1024))
            await member.send(embed=dm)
        except discord.HTTPException:
            pass

    await log_result(
        bot, guild, cfg, member or interaction.user,
        "approved" if approve else "denied", interaction.user, reason, request_id,
    )
    await utils.safe_respond(
        interaction,
        f"{utils.OK} Anfrage #{request_id} {'angenommen' if approve else 'abgelehnt'}."
        + (f"\n{utils.WARN} " + "\n".join(errors) if errors else ""),
    )


class DenyReasonModal(discord.ui.Modal, title="Verifizierung ablehnen"):
    reason = discord.ui.TextInput(
        label="Grund (wird dem Nutzer per DM geschickt)",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=900,
        placeholder="z. B. Formular unvollständig ausgefüllt",
    )

    def __init__(self, request_id: int):
        super().__init__(timeout=600)
        self.request_id = request_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await handle_decision(
            interaction, self.request_id, approve=False,
            reason=(self.reason.value or "").strip() or None,
        )


class AcceptButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"vs:req:(?P<rid>\d+):accept",
):
    def __init__(self, request_id: int):
        self.request_id = request_id
        super().__init__(
            discord.ui.Button(
                label="Annehmen",
                style=discord.ButtonStyle.success,
                emoji="✅",
                custom_id=f"vs:req:{request_id}:accept",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match):  # type: ignore[override]
        return cls(int(match["rid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        await handle_decision(interaction, self.request_id, approve=True)


class DenyButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"vs:req:(?P<rid>\d+):deny",
):
    def __init__(self, request_id: int):
        self.request_id = request_id
        super().__init__(
            discord.ui.Button(
                label="Ablehnen",
                style=discord.ButtonStyle.danger,
                emoji="❌",
                custom_id=f"vs:req:{request_id}:deny",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match):  # type: ignore[override]
        return cls(int(match["rid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        bot = interaction.client
        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            return
        cfg = await bot.get_cfg(guild.id)
        if not utils.is_staff(interaction.user, cfg):
            await interaction.response.send_message(
                f"{utils.NO} Du darfst Verifizierungen nicht bearbeiten.", ephemeral=True
            )
            return
        await interaction.response.send_modal(DenyReasonModal(self.request_id))


class ReviewView(discord.ui.View):
    """Buttons unter einer Anfrage – dank DynamicItem nach Neustart weiter gültig."""

    def __init__(self, request_id: int):
        super().__init__(timeout=None)
        self.add_item(AcceptButton(request_id))
        self.add_item(DenyButton(request_id))


# ============================================== Verifizieren-Panel (Button) ===
class RetryOpenView(discord.ui.View):
    """Fallback, falls die Daten nicht schnell genug geladen wurden."""

    def __init__(self, bot, user_id: int):
        super().__init__(timeout=300)
        self.bot = bot
        self.user_id = user_id

    @discord.ui.button(label="Formular öffnen", style=discord.ButtonStyle.success, emoji="📝")
    async def open(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(f"{utils.NO} Nicht für dich.", ephemeral=True)
            return
        await start_verification(self.bot, interaction, from_retry=True)


async def start_verification(bot, interaction: discord.Interaction, from_retry: bool = False) -> None:
    guild = interaction.guild
    user = interaction.user
    if guild is None or not isinstance(user, discord.Member):
        await utils.safe_respond(interaction, f"{utils.NO} Das geht nur auf einem Server.")
        return

    data = bot.cache.get(guild.id)
    if data is None:
        # Noch nichts im Cache -> nachladen, ohne die Interaction verfallen zu lassen.
        await interaction.response.send_message(
            "⏳ Einen Moment – ich lade die Verifizierung…", ephemeral=True
        )
        data = await bot.load_guild(guild.id)
        view = RetryOpenView(bot, user.id)
        await interaction.edit_original_response(
            content="Klicke auf **Formular öffnen**, um fortzufahren.", view=view
        )
        return

    cfg, fields, checkboxes = data["config"], data["fields"], data["checkboxes"]

    pending = await bot.db.get_pending_request(guild.id, user.id)
    if pending:
        await utils.safe_respond(
            interaction,
            f"{utils.WARN} Du hast bereits eine offene Anfrage (**#{pending['id']}**). "
            "Bitte warte auf die Prüfung durch das Team.",
        )
        return

    add_ids = set(as_ids(cfg.get("add_role_ids")))
    if add_ids and add_ids.issubset({r.id for r in user.roles}):
        await utils.safe_respond(interaction, f"{utils.OK} Du bist bereits verifiziert.")
        return

    try:
        already = await bot.db.is_verified(guild.id, user.id)
    except Exception:  # noqa: BLE001
        already = False
    if already:
        await utils.safe_respond(
            interaction,
            f"{utils.OK} Du wurdest auf diesem Server **bereits verifiziert**. "
            "Wenn dir Rollen fehlen, melde dich bitte beim Team.",
        )
        return

    if not cfg.get("requests_channel_id") and not cfg.get("auto_approve"):
        await utils.safe_respond(
            interaction,
            f"{utils.NO} Die Verifizierung ist noch nicht fertig eingerichtet "
            "(kein Anfragen-Kanal). Bitte melde dich beim Team.",
        )
        return

    if fields or checkboxes:
        await interaction.response.send_modal(VerifyFormModal(bot, fields, checkboxes))
        return

    # Weder Formular noch Checkboxen im Cache -> sicherheitshalber frisch laden,
    # bevor die Anfrage ohne Fragen rausgeht.
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        fresh = await bot.load_guild(guild.id)
        fields, checkboxes = fresh["fields"], fresh["checkboxes"]
    except Exception:  # noqa: BLE001
        log.exception("Nachladen der Verify-Daten fehlgeschlagen.")
        fields, checkboxes = [], []

    if fields:
        await interaction.edit_original_response(
            content="Klicke auf **Formular öffnen**, um fortzufahren.",
            view=RetryOpenView(bot, user.id),
        )
        return

    if checkboxes:
        await interaction.edit_original_response(
            content="Klicke auf **Formular öffnen**, um fortzufahren.",
            view=RetryOpenView(bot, user.id),
        )
        return

    await submit_request(bot, interaction, [], [])


async def send_verify_panel(
    bot,
    guild: discord.Guild,
    cfg: dict[str, Any],
    channel: discord.abc.GuildChannel | None = None,
) -> tuple[discord.Message | None, str]:
    """Sendet das Verify-Embed. Gibt (Nachricht|None, Info-/Fehlertext) zurück.

    Meldet jeden Fehlerfall als lesbaren Text zurück, damit im Setup niemals
    "einfach nichts" passiert.
    """
    target = channel
    if target is None:
        channel_id = cfg.get("panel_channel_id")
        if not channel_id:
            return None, (
                f"{utils.NO} Es ist kein **Verify-Kanal** eingestellt. "
                "Wähle ihn im Setup unter *Verify-Kanal* aus."
            )
        target = guild.get_channel(int(channel_id))
        if target is None:
            try:
                target = await bot.fetch_channel(int(channel_id))
            except discord.HTTPException:
                target = None
        if target is None:
            return None, (
                f"{utils.NO} Den eingestellten Verify-Kanal (`{channel_id}`) finde ich nicht mehr. "
                "Bitte wähle im Setup einen neuen Kanal aus."
            )

    if not isinstance(target, (discord.TextChannel, discord.Thread)):
        return None, f"{utils.NO} Der Verify-Kanal muss ein normaler Textkanal sein."

    me = guild.me
    if me is None and bot.user is not None:
        try:
            me = await guild.fetch_member(bot.user.id)
        except discord.HTTPException:
            me = None
    if me is not None:
        perms = target.permissions_for(me)
        missing = [
            name for ok, name in (
                (perms.view_channel, "Kanal ansehen"),
                (perms.send_messages, "Nachrichten senden"),
                (perms.embed_links, "Links einbetten"),
            ) if not ok
        ]
        if missing:
            return None, (
                f"{utils.NO} Mir fehlen Rechte in {target.mention}: "
                + ", ".join(f"`{m}`" for m in missing)
            )

    # Alte/vorhandene Panel-Nachricht behandeln
    embed = utils.build_panel_embed(cfg, guild)
    view = VerifyPanelView(bot, cfg)
    old_id = cfg.get("panel_message_id")
    # Wo liegt die alte Nachricht wirklich? (Der Ziel-Kanal kann inzwischen
    # geaendert worden sein.)
    old_channel_id = cfg.get("panel_message_channel_id") or cfg.get("panel_channel_id")

    # 1) Existiert das Embed schon im selben Kanal? -> einfach bearbeiten
    if old_id and old_channel_id and int(old_channel_id) == target.id:
        existing = None
        try:
            existing = await target.fetch_message(int(old_id))
        except (discord.HTTPException, AttributeError):
            existing = None
        own = (
            existing is not None
            and bot.user is not None
            and getattr(getattr(existing, "author", None), "id", None) == bot.user.id
        )
        if existing is not None and own:
            try:
                await existing.edit(content=None, embed=embed, view=view)
            except discord.HTTPException:
                log.warning("Panel konnte nicht bearbeitet werden – sende neu.")
            else:
                await bot.update_cfg(
                    guild.id, panel_channel_id=target.id,
                    panel_message_id=existing.id, panel_message_channel_id=target.id,
                    setup_completed=True,
                )
                await bot.load_guild(guild.id)
                return existing, (
                    f"{utils.OK} Das vorhandene Verify-Embed in {target.mention} wurde "
                    f"**aktualisiert**: {existing.jump_url}"
                )

    # 2) Panel lag in einem anderen Kanal -> dort aufräumen
    if old_id and old_channel_id and int(old_channel_id) != target.id:
        old_channel = guild.get_channel(int(old_channel_id))
        if isinstance(old_channel, (discord.TextChannel, discord.Thread)):
            try:
                old = await old_channel.fetch_message(int(old_id))
                await old.delete()
            except (discord.HTTPException, AttributeError):
                pass

    # 3) Neu senden
    try:
        message = await target.send(embed=embed, view=view)
    except discord.Forbidden as exc:
        return None, (
            f"{utils.NO} Discord hat das Senden in {target.mention} verweigert "
            f"(keine Berechtigung): `{exc.text or exc}`"
        )
    except discord.HTTPException as exc:
        log.exception("Panel konnte nicht gesendet werden.")
        return None, f"{utils.NO} Fehler beim Senden: `{exc}`"

    await bot.update_cfg(
        guild.id,
        panel_channel_id=target.id,
        panel_message_id=message.id,
        panel_message_channel_id=target.id,
        setup_completed=True,
    )
    await bot.load_guild(guild.id)
    return message, (
        f"{utils.OK} Verify-Embed wurde in {target.mention} gesendet: {message.jump_url}\n"
        "Der Button bleibt dauerhaft gültig – auch nach einem Neustart des Bots."
    )


class VerifyPanelView(discord.ui.View):
    """Persistente View unter dem Verify-Embed (timeout=None => immer gültig)."""

    def __init__(self, bot, cfg: dict[str, Any] | None = None):
        super().__init__(timeout=None)
        cfg = cfg or {}
        button = discord.ui.Button(
            label=utils.truncate(str(cfg.get("button_label") or config.DEFAULT_BUTTON_LABEL), 80),
            style=utils.button_style_of(cfg),
            custom_id=config.VERIFY_BUTTON_ID,
        )
        emoji = utils.clean_emoji(cfg.get("button_emoji") or config.DEFAULT_BUTTON_EMOJI)
        if emoji:
            try:
                button.emoji = discord.PartialEmoji.from_str(emoji)
            except Exception:  # noqa: BLE001 - ungültiges Emoji einfach ignorieren
                pass
        button.callback = self._callback  # type: ignore[assignment]
        self.bot = bot
        self.add_item(button)

    async def _callback(self, interaction: discord.Interaction) -> None:
        try:
            await start_verification(self.bot, interaction)
        except Exception:  # noqa: BLE001
            log.exception("Fehler beim Start der Verifizierung")
            await utils.safe_respond(
                interaction,
                f"{utils.NO} Da ist etwas schiefgelaufen. Bitte versuche es gleich noch einmal.",
            )
