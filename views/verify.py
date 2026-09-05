"""Alles rund um den Verifizieren-Button, Formular, Checkboxen und die Team-Prüfung."""
from __future__ import annotations

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
class VerifyFormModal(discord.ui.Modal):
    """Dynamisch aus den konfigurierten Formularfeldern gebautes Modal."""

    def __init__(self, bot, fields: list[dict[str, Any]], checkboxes: list[dict[str, Any]]):
        super().__init__(title="Verifizierung", timeout=900)
        self.bot = bot
        self.fields = fields[: config.MAX_FORM_FIELDS]
        self.checkboxes = checkboxes
        self.inputs: list[discord.ui.TextInput] = []

        for field in self.fields:
            item = discord.ui.TextInput(
                label=utils.truncate(str(field["label"]), 45),
                placeholder=(field.get("placeholder") or None),
                style=(
                    discord.TextStyle.paragraph
                    if str(field.get("style")) == "paragraph"
                    else discord.TextStyle.short
                ),
                required=bool(field.get("required", True)),
                max_length=int(field["max_length"]) if field.get("max_length") else None,
            )
            self.inputs.append(item)
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        answers = [
            {"label": str(f["label"]), "value": (i.value or "").strip()}
            for f, i in zip(self.fields, self.inputs)
        ]

        if self.checkboxes:
            view = CheckboxView(self.bot, interaction.user.id, self.checkboxes, answers)
            await interaction.response.send_message(
                embed=view.build_embed(), view=view, ephemeral=True
            )
            view.message = await interaction.original_response()
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        await submit_request(self.bot, interaction, answers, [])

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("Fehler im Verify-Formular", exc_info=error)
        await utils.safe_respond(
            interaction,
            f"{utils.NO} Beim Absenden ist ein Fehler aufgetreten. Bitte versuche es erneut.",
        )


# ============================================================= Checkboxen ====
class CheckboxButton(discord.ui.Button["CheckboxView"]):
    def __init__(self, index: int, data: dict[str, Any], row: int):
        self.index = index
        self.data = data
        super().__init__(
            label=utils.truncate(str(data["label"]), 78),
            style=discord.ButtonStyle.secondary,
            emoji=CHECK_OFF,
            row=row,
            custom_id=f"cb:{index}",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        assert view is not None
        view.state[self.index] = not view.state[self.index]
        checked = view.state[self.index]
        self.emoji = discord.PartialEmoji.from_str(CHECK_ON if checked else CHECK_OFF)
        self.style = discord.ButtonStyle.success if checked else discord.ButtonStyle.secondary
        await interaction.response.edit_message(embed=view.build_embed(), view=view)


class CheckboxView(discord.ui.View):
    """Ephemere Ansicht mit an-/abwählbaren Checkbox-Buttons."""

    def __init__(self, bot, user_id: int, checkboxes: list[dict[str, Any]],
                 answers: list[dict[str, Any]]):
        super().__init__(timeout=900)
        self.bot = bot
        self.user_id = user_id
        self.checkboxes = checkboxes[: config.MAX_CHECKBOXES]
        self.answers = answers
        self.state = [False] * len(self.checkboxes)
        self.message: discord.Message | None = None

        for index, data in enumerate(self.checkboxes):
            self.add_item(CheckboxButton(index, data, row=index // 5))

        self.submit_button = discord.ui.Button(
            label="Absenden", style=discord.ButtonStyle.success, emoji="📨", row=4
        )
        self.submit_button.callback = self.on_submit  # type: ignore[assignment]
        self.add_item(self.submit_button)

        cancel = discord.ui.Button(label="Abbrechen", style=discord.ButtonStyle.danger, row=4)
        cancel.callback = self.on_cancel  # type: ignore[assignment]
        self.add_item(cancel)

    # ------------------------------------------------------------------ ui --
    def build_embed(self) -> discord.Embed:
        lines = []
        for index, data in enumerate(self.checkboxes):
            mark = CHECK_ON if self.state[index] else CHECK_OFF
            pflicht = " *(Pflicht)*" if data.get("required", True) else " *(optional)*"
            lines.append(f"{mark} **{data['label']}**{pflicht}")
            if data.get("description"):
                lines.append(f"┕ {data['description']}")
        embed = discord.Embed(
            title="📋 Bitte bestätigen",
            description="\n".join(lines) or "*(keine Checkboxen)*",
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="Klicke die Punkte an und danach auf „Absenden“.")
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                f"{utils.NO} Das ist nicht deine Verifizierung.", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(
                    content="⌛ Zeit abgelaufen – klicke erneut auf **Verifizieren**.",
                    view=self,
                )
            except discord.HTTPException:
                pass

    # -------------------------------------------------------------- actions --
    async def on_cancel(self, interaction: discord.Interaction) -> None:
        self.stop()
        await interaction.response.edit_message(
            content="Abgebrochen.", embed=None, view=None
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        missing = [
            data["label"]
            for index, data in enumerate(self.checkboxes)
            if data.get("required", True) and not self.state[index]
        ]
        if missing:
            await interaction.response.send_message(
                f"{utils.WARN} Du musst noch bestätigen:\n"
                + "\n".join(f"• {m}" for m in missing),
                ephemeral=True,
            )
            return

        checks = [
            {"label": str(d["label"]), "checked": self.state[i], "required": bool(d.get("required", True))}
            for i, d in enumerate(self.checkboxes)
        ]
        self.stop()
        await interaction.response.edit_message(
            content="⏳ Anfrage wird gesendet…", embed=None, view=None
        )
        await submit_request(self.bot, interaction, self.answers, checks, edit_original=True)


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

    if not cfg.get("requests_channel_id") and not cfg.get("auto_approve"):
        await utils.safe_respond(
            interaction,
            f"{utils.NO} Die Verifizierung ist noch nicht fertig eingerichtet "
            "(kein Anfragen-Kanal). Bitte melde dich beim Team.",
        )
        return

    if fields:
        await interaction.response.send_modal(VerifyFormModal(bot, fields, checkboxes))
        return

    if checkboxes:
        view = CheckboxView(bot, user.id, checkboxes, [])
        await interaction.response.send_message(embed=view.build_embed(), view=view, ephemeral=True)
        view.message = await interaction.original_response()
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    await submit_request(bot, interaction, [], [])


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
