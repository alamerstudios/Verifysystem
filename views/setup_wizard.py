"""Das `/setup`-Menü.

* **SetupWizard** – Erst-Einrichtung: stellt Schritt für Schritt alle Fragen.
* **SetupPanelView** – Verwaltungs-Menü, sobald das Setup abgeschlossen ist:
  hier lassen sich Kanäle, Rollen, Embed, Button, Formulare und Checkboxen
  einzeln bearbeiten, ohne den ganzen Assistenten erneut durchlaufen zu müssen.
"""
from __future__ import annotations

import logging
from typing import Any

import discord

import config
import utils
from database import as_ids
from views.verify import VerifyPanelView, send_verify_panel

log = logging.getLogger("verifybot.setup")

STEP_TITLES = [
    "Start",
    "Verify-Kanal",
    "Anfragen-Kanal",
    "Team-Rollen",
    "Rollen hinzufügen",
    "Rollen entfernen",
    "Auto-Rollen",
    "Embed",
    "Bewerbung",
    "Fertig",
]
LAST_STEP = len(STEP_TITLES) - 1

# key -> (Anzeigename, Typ, Emoji, Erklärung)
SECTIONS: dict[str, tuple[str, str, str, str]] = {
    "panel_channel_id": (
        "Verify-Kanal", "channel", "📢",
        "Hier wird das Embed mit dem **Verifizieren**-Button gepostet.",
    ),
    "requests_channel_id": (
        "Anfragen-Kanal", "channel", "📥",
        "Hier landen alle Verifizierungs-Anfragen mit **Annehmen/Ablehnen**.",
    ),
    "staff_role_ids": (
        "Team-Rollen", "roles", "🛡️",
        "Diese Rollen dürfen Anfragen bearbeiten und werden dabei gepingt.",
    ),
    "add_role_ids": (
        "Rollen hinzufügen", "roles", "➕",
        "Diese Rollen bekommt ein Nutzer, sobald er verifiziert wurde.",
    ),
    "remove_role_ids": (
        "Rollen entfernen", "roles", "➖",
        "Diese Rollen werden ihm dabei **abgezogen** (z. B. „Unverifiziert“).",
    ),
    "autorole_ids": (
        "Auto-Rollen beim Join", "roles", "🚪",
        "Diese Rollen bekommt **jeder neue Nutzer** automatisch beim Serverbeitritt.",
    ),
}


# ============================================================ Basis-View =====
class BaseSetupView(discord.ui.View):
    """Gemeinsame Logik von Assistent, Verwaltungs-Menü und Unter-Menüs."""

    def __init__(self, bot, guild: discord.Guild, author_id: int, timeout: float = 1200):
        super().__init__(timeout=timeout)
        self.bot = bot
        self.guild = guild
        self.guild_id = guild.id
        self.author_id = author_id
        self.cfg: dict[str, Any] = {}
        self.message: discord.Message | None = None

    # ------------------------------------------------------------- helpers --
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                f"{utils.NO} Dieses Setup-Menü gehört nicht dir.", ephemeral=True
            )
            return False
        return True

    async def on_timeout(self) -> None:
        if self.message is not None:
            try:
                await self.message.edit(
                    content="⌛ Setup-Menü abgelaufen – führe `/setup` erneut aus.", view=None
                )
            except discord.HTTPException:
                pass

    async def save(self, **values: Any) -> None:
        self.cfg = await self.bot.update_cfg(self.guild_id, **values)

    async def edit(self, interaction: discord.Interaction, **kwargs: Any) -> None:
        try:
            if interaction.response.is_done():
                await interaction.edit_original_response(**kwargs)
            else:
                await interaction.response.edit_message(**kwargs)
        except discord.HTTPException:
            log.exception("Setup-Nachricht konnte nicht aktualisiert werden.")

    async def render(self, interaction: discord.Interaction) -> None:
        self.cfg = await self.bot.get_cfg(self.guild_id)
        embed = await self.build_embed()
        self.rebuild()
        await self.edit(interaction, content=None, embed=embed, view=self)

    async def start(self, interaction: discord.Interaction) -> None:
        self.cfg = await self.bot.get_cfg(self.guild_id)
        embed = await self.build_embed()
        self.rebuild()
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, view=self, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, view=self, ephemeral=True)
        try:
            self.message = await interaction.original_response()
        except discord.HTTPException:
            self.message = None

    def permission_warnings(self) -> list[str]:
        """Sagt frühzeitig Bescheid, wenn dem Bot Rechte in den Kanälen fehlen."""
        warnings = []
        for key, label in (
            ("panel_channel_id", "Verify-Kanal"),
            ("requests_channel_id", "Anfragen-Kanal"),
        ):
            problem = utils.channel_problem(self.guild, self.cfg.get(key), label)
            if problem:
                warnings.append(problem)
        return warnings

    async def build_embed(self) -> discord.Embed:  # pragma: no cover - Interface
        raise NotImplementedError

    def rebuild(self) -> None:  # pragma: no cover - Interface
        raise NotImplementedError

    # --------------------------------------------------------- Panel senden --
    async def do_send_panel(self, interaction: discord.Interaction) -> None:
        if not interaction.response.is_done():
            await interaction.response.defer()
        try:
            cfg = await self.bot.get_cfg(self.guild_id)
            _message, info = await send_verify_panel(self.bot, self.guild, cfg)
        except Exception as exc:  # noqa: BLE001
            log.exception("Panel senden fehlgeschlagen")
            info = f"{utils.NO} Unerwarteter Fehler: `{exc}`"

        await self.render(interaction)
        try:
            await interaction.followup.send(info, ephemeral=True)
        except discord.HTTPException:
            log.exception("Rückmeldung zum Panel-Senden fehlgeschlagen.")

    # ------------------------------------------------------ Select-Bausteine --
    def make_channel_select(self, key: str, placeholder: str, row: int = 0,
                            after=None) -> discord.ui.ChannelSelect:
        select = discord.ui.ChannelSelect(
            placeholder=placeholder,
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            min_values=1, max_values=1, row=row,
        )

        async def callback(interaction: discord.Interaction) -> None:
            await self.save(**{key: select.values[0].id})
            if after is not None:
                await after(interaction)
            else:
                await self.render(interaction)

        select.callback = callback  # type: ignore[assignment]
        return select

    def make_role_select(self, key: str, placeholder: str, row: int = 0,
                         after=None) -> discord.ui.RoleSelect:
        current = as_ids(self.cfg.get(key))
        select = discord.ui.RoleSelect(
            placeholder=placeholder, min_values=0, max_values=15, row=row,
            default_values=[discord.Object(id=r) for r in current][:15],
        )

        async def callback(interaction: discord.Interaction) -> None:
            await self.save(**{key: [r.id for r in select.values]})
            if after is not None:
                await after(interaction)
            else:
                await self.render(interaction)

        select.callback = callback  # type: ignore[assignment]
        return select


# ================================================================ Modals =====
class EmbedEditModal(discord.ui.Modal, title="Embed bearbeiten"):
    def __init__(self, parent: BaseSetupView, cfg: dict[str, Any]):
        super().__init__(timeout=900)
        self.parent = parent
        self.f_title = discord.ui.TextInput(
            label="Titel", max_length=256, required=False,
            default=cfg.get("embed_title") or config.DEFAULT_EMBED_TITLE,
        )
        self.f_desc = discord.ui.TextInput(
            label="Beschreibung", style=discord.TextStyle.paragraph, max_length=3000,
            required=False, default=cfg.get("embed_description") or config.DEFAULT_EMBED_DESCRIPTION,
        )
        self.f_color = discord.ui.TextInput(
            label="Farbe (Hex, z. B. 5865F2 oder gruen)", max_length=20, required=False,
            default=f"{int(cfg.get('embed_color') or config.DEFAULT_EMBED_COLOR):06X}",
        )
        self.f_footer = discord.ui.TextInput(
            label="Footer (leer = keiner)", max_length=200, required=False,
            default=cfg.get("embed_footer") or "",
        )
        self.f_image = discord.ui.TextInput(
            label="Bild-URL (leer = keins)", max_length=500, required=False,
            default=cfg.get("embed_image_url") or "",
            placeholder="https://…  (Banner unter dem Text)",
        )
        for item in (self.f_title, self.f_desc, self.f_color, self.f_footer, self.f_image):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.parent.save(
            embed_title=(self.f_title.value or "").strip() or config.DEFAULT_EMBED_TITLE,
            embed_description=(self.f_desc.value or "").strip() or config.DEFAULT_EMBED_DESCRIPTION,
            embed_color=utils.parse_color(self.f_color.value),
            embed_footer=(self.f_footer.value or "").strip() or None,
            embed_image_url=(self.f_image.value or "").strip() or None,
        )
        await self.parent.render(interaction)


class ButtonEditModal(discord.ui.Modal, title="Button bearbeiten"):
    def __init__(self, parent: BaseSetupView, cfg: dict[str, Any]):
        super().__init__(timeout=900)
        self.parent = parent
        self.f_label = discord.ui.TextInput(
            label="Button-Text", max_length=80, required=True,
            default=cfg.get("button_label") or config.DEFAULT_BUTTON_LABEL,
        )
        self.f_emoji = discord.ui.TextInput(
            label="Emoji (leer = keins)", max_length=64, required=False,
            default=cfg.get("button_emoji") or "",
        )
        self.f_style = discord.ui.TextInput(
            label="Farbe: gruen / blau / grau / rot", max_length=20, required=False,
            default=cfg.get("button_style") or config.DEFAULT_BUTTON_STYLE,
        )
        for item in (self.f_label, self.f_emoji, self.f_style):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        style_map = {
            "gruen": "success", "grün": "success", "green": "success", "success": "success",
            "blau": "primary", "blue": "primary", "blurple": "primary", "primary": "primary",
            "grau": "secondary", "grey": "secondary", "gray": "secondary", "secondary": "secondary",
            "rot": "danger", "red": "danger", "danger": "danger",
        }
        style = style_map.get((self.f_style.value or "").strip().lower(), "success")
        await self.parent.save(
            button_label=(self.f_label.value or config.DEFAULT_BUTTON_LABEL).strip(),
            button_emoji=utils.clean_emoji(self.f_emoji.value),
            button_style=style,
        )
        await self.parent.render(interaction)


class FormFieldModal(discord.ui.Modal):
    """Formularfeld anlegen oder bearbeiten."""

    def __init__(self, manager: "FormManagerView", field: dict[str, Any] | None = None):
        super().__init__(title="Frage bearbeiten" if field else "Frage hinzufügen", timeout=900)
        self.manager = manager
        self.field = field
        self.f_label = discord.ui.TextInput(
            label="Frage / Beschriftung", max_length=45, required=True,
            default=(field or {}).get("label") or "",
            placeholder="z. B. Wie alt bist du?",
        )
        self.f_placeholder = discord.ui.TextInput(
            label="Platzhalter (optional)", max_length=100, required=False,
            default=(field or {}).get("placeholder") or "",
        )
        self.f_style = discord.ui.TextInput(
            label="Länge: kurz / lang", max_length=10, required=False,
            default="lang" if (field or {}).get("style") == "paragraph" else "kurz",
        )
        self.f_required = discord.ui.TextInput(
            label="Pflichtfeld? ja / nein", max_length=5, required=False,
            default="ja" if (field or {}).get("required", True) else "nein",
        )
        self.f_max = discord.ui.TextInput(
            label="Max. Zeichen (leer = kein Limit)", max_length=5, required=False,
            default=str((field or {}).get("max_length") or ""),
        )
        for item in (self.f_label, self.f_placeholder, self.f_style, self.f_required, self.f_max):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        style = "paragraph" if (self.f_style.value or "").strip().lower().startswith(("l", "p")) else "short"
        required = not (self.f_required.value or "ja").strip().lower().startswith(("n", "f"))
        raw_max = (self.f_max.value or "").strip()
        max_length = int(raw_max) if raw_max.isdigit() and 1 <= int(raw_max) <= 4000 else None
        db = self.manager.parent.bot.db
        guild_id = self.manager.parent.guild_id

        if self.field:
            await db.update_form_field(
                int(self.field["id"]),
                label=self.f_label.value.strip(),
                placeholder=(self.f_placeholder.value or "").strip() or None,
                style=style, required=required, max_length=max_length,
            )
        else:
            await db.add_form_field(
                guild_id, self.f_label.value.strip(),
                (self.f_placeholder.value or "").strip() or None,
                style, required, max_length,
            )
        await self.manager.parent.bot.load_guild(guild_id)
        await self.manager.refresh(interaction)


class CheckboxModal(discord.ui.Modal):
    def __init__(self, manager: "CheckboxManagerView", box: dict[str, Any] | None = None):
        super().__init__(title="Checkbox bearbeiten" if box else "Checkbox hinzufügen", timeout=900)
        self.manager = manager
        self.box = box
        self.f_label = discord.ui.TextInput(
            label="Text der Checkbox", max_length=78, required=True,
            default=(box or {}).get("label") or "",
            placeholder="z. B. Ich akzeptiere die Serverregeln",
        )
        self.f_desc = discord.ui.TextInput(
            label="Zusatztext (optional)", style=discord.TextStyle.paragraph,
            max_length=200, required=False, default=(box or {}).get("description") or "",
        )
        self.f_required = discord.ui.TextInput(
            label="Muss angehakt werden? ja / nein", max_length=5, required=False,
            default="ja" if (box or {}).get("required", True) else "nein",
        )
        for item in (self.f_label, self.f_desc, self.f_required):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        required = not (self.f_required.value or "ja").strip().lower().startswith(("n", "f"))
        db = self.manager.parent.bot.db
        guild_id = self.manager.parent.guild_id
        if self.box:
            await db.update_checkbox(
                int(self.box["id"]),
                label=self.f_label.value.strip(),
                description=(self.f_desc.value or "").strip() or None,
                required=required,
            )
        else:
            await db.add_checkbox(
                guild_id, self.f_label.value.strip(),
                (self.f_desc.value or "").strip() or None, required,
            )
        await self.manager.parent.bot.load_guild(guild_id)
        await self.manager.refresh(interaction)


# ==================================================== Manager (Formular) =====
class FormManagerView(discord.ui.View):
    def __init__(self, parent: BaseSetupView):
        super().__init__(timeout=900)
        self.parent = parent
        self.selected: int | None = None
        self.fields: list[dict[str, Any]] = []

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await self.parent.interaction_check(interaction)

    async def load(self) -> None:
        self.fields = await self.parent.bot.db.get_form_fields(self.parent.guild_id)
        if self.selected not in {int(f["id"]) for f in self.fields}:
            self.selected = None

    def build_embed(self) -> discord.Embed:
        lines = []
        for index, field in enumerate(self.fields, start=1):
            flag = "Pflicht" if field.get("required", True) else "optional"
            art = "mehrzeilig" if field.get("style") == "paragraph" else "einzeilig"
            marker = "▶️ " if self.selected == int(field["id"]) else ""
            lines.append(f"{marker}**{index}. {field['label']}** — *{art}, {flag}*")
            if field.get("placeholder"):
                lines.append(f"┕ Platzhalter: `{field['placeholder']}`")
        embed = discord.Embed(
            title="📝 Formular-Fragen",
            description=(
                "\n".join(lines)
                or "*Noch keine Fragen angelegt.*\n\nKlicke auf **➕ Frage hinzufügen**."
            ),
            color=discord.Color.blurple(),
        )
        embed.set_footer(
            text=f"{len(self.fields)}/{config.MAX_FORM_FIELDS} Fragen "
                 "• Discord erlaubt max. 5 Felder pro Formular."
        )
        return embed

    def rebuild(self) -> None:
        self.clear_items()
        if self.fields:
            options = [
                discord.SelectOption(
                    label=utils.truncate(f["label"], 90),
                    value=str(f["id"]),
                    default=self.selected == int(f["id"]),
                )
                for f in self.fields
            ]
            select = discord.ui.Select(
                placeholder="Frage auswählen zum Bearbeiten/Löschen", options=options
            )
            select.callback = self._on_select  # type: ignore[assignment]
            self.add_item(select)

        add = discord.ui.Button(
            label="Frage hinzufügen", style=discord.ButtonStyle.success, emoji="➕", row=1,
            disabled=len(self.fields) >= config.MAX_FORM_FIELDS,
        )
        add.callback = self._on_add  # type: ignore[assignment]
        self.add_item(add)

        edit = discord.ui.Button(
            label="Bearbeiten", style=discord.ButtonStyle.primary, emoji="✏️", row=1,
            disabled=self.selected is None,
        )
        edit.callback = self._on_edit  # type: ignore[assignment]
        self.add_item(edit)

        delete = discord.ui.Button(
            label="Löschen", style=discord.ButtonStyle.danger, emoji="🗑️", row=1,
            disabled=self.selected is None,
        )
        delete.callback = self._on_delete  # type: ignore[assignment]
        self.add_item(delete)

        back = discord.ui.Button(label="Zurück", style=discord.ButtonStyle.secondary,
                                 emoji="⬅️", row=2)
        back.callback = self._on_back  # type: ignore[assignment]
        self.add_item(back)

    async def refresh(self, interaction: discord.Interaction) -> None:
        await self.load()
        self.rebuild()
        await self.parent.edit(interaction, content=None, embed=self.build_embed(), view=self)

    # ------------------------------------------------------------ callbacks --
    async def _on_select(self, interaction: discord.Interaction) -> None:
        self.selected = int(interaction.data["values"][0])  # type: ignore[index]
        await self.refresh(interaction)

    async def _on_add(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(FormFieldModal(self))

    async def _on_edit(self, interaction: discord.Interaction) -> None:
        field = next((f for f in self.fields if int(f["id"]) == self.selected), None)
        if field is None:
            await self.refresh(interaction)
            return
        await interaction.response.send_modal(FormFieldModal(self, field))

    async def _on_delete(self, interaction: discord.Interaction) -> None:
        if self.selected is not None:
            await self.parent.bot.db.delete_form_field(self.selected)
            self.selected = None
            await self.parent.bot.load_guild(self.parent.guild_id)
        await self.refresh(interaction)

    async def _on_back(self, interaction: discord.Interaction) -> None:
        self.stop()
        await self.parent.render(interaction)


# =================================================== Manager (Checkboxen) ====
class CheckboxManagerView(discord.ui.View):
    def __init__(self, parent: BaseSetupView):
        super().__init__(timeout=900)
        self.parent = parent
        self.selected: int | None = None
        self.boxes: list[dict[str, Any]] = []

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await self.parent.interaction_check(interaction)

    async def load(self) -> None:
        self.boxes = await self.parent.bot.db.get_checkboxes(self.parent.guild_id)
        if self.selected not in {int(b["id"]) for b in self.boxes}:
            self.selected = None

    def build_embed(self) -> discord.Embed:
        lines = []
        for index, box in enumerate(self.boxes, start=1):
            flag = "Pflicht" if box.get("required", True) else "optional"
            marker = "▶️ " if self.selected == int(box["id"]) else ""
            lines.append(f"{marker}**{index}. ☑️ {box['label']}** — *{flag}*")
            if box.get("description"):
                lines.append(f"┕ {box['description']}")
        embed = discord.Embed(
            title="☑️ Checkboxen",
            description=(
                "\n".join(lines)
                or "*Noch keine Checkboxen angelegt.*\n\nKlicke auf **➕ Checkbox hinzufügen**."
            ),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"{len(self.boxes)}/{config.MAX_CHECKBOXES} Checkboxen")
        return embed

    def rebuild(self) -> None:
        self.clear_items()
        if self.boxes:
            options = [
                discord.SelectOption(
                    label=utils.truncate(b["label"], 90),
                    value=str(b["id"]),
                    default=self.selected == int(b["id"]),
                )
                for b in self.boxes
            ]
            select = discord.ui.Select(
                placeholder="Checkbox auswählen zum Bearbeiten/Löschen", options=options
            )
            select.callback = self._on_select  # type: ignore[assignment]
            self.add_item(select)

        add = discord.ui.Button(
            label="Checkbox hinzufügen", style=discord.ButtonStyle.success, emoji="➕", row=1,
            disabled=len(self.boxes) >= config.MAX_CHECKBOXES,
        )
        add.callback = self._on_add  # type: ignore[assignment]
        self.add_item(add)

        edit = discord.ui.Button(
            label="Text bearbeiten", style=discord.ButtonStyle.primary, emoji="✏️", row=1,
            disabled=self.selected is None,
        )
        edit.callback = self._on_edit  # type: ignore[assignment]
        self.add_item(edit)

        delete = discord.ui.Button(
            label="Löschen", style=discord.ButtonStyle.danger, emoji="🗑️", row=1,
            disabled=self.selected is None,
        )
        delete.callback = self._on_delete  # type: ignore[assignment]
        self.add_item(delete)

        back = discord.ui.Button(label="Zurück", style=discord.ButtonStyle.secondary,
                                 emoji="⬅️", row=2)
        back.callback = self._on_back  # type: ignore[assignment]
        self.add_item(back)

    async def refresh(self, interaction: discord.Interaction) -> None:
        await self.load()
        self.rebuild()
        await self.parent.edit(interaction, content=None, embed=self.build_embed(), view=self)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        self.selected = int(interaction.data["values"][0])  # type: ignore[index]
        await self.refresh(interaction)

    async def _on_add(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(CheckboxModal(self))

    async def _on_edit(self, interaction: discord.Interaction) -> None:
        box = next((b for b in self.boxes if int(b["id"]) == self.selected), None)
        if box is None:
            await self.refresh(interaction)
            return
        await interaction.response.send_modal(CheckboxModal(self, box))

    async def _on_delete(self, interaction: discord.Interaction) -> None:
        if self.selected is not None:
            await self.parent.bot.db.delete_checkbox(self.selected)
            self.selected = None
            await self.parent.bot.load_guild(self.parent.guild_id)
        await self.refresh(interaction)

    async def _on_back(self, interaction: discord.Interaction) -> None:
        self.stop()
        await self.parent.render(interaction)


# ============================================ Einzel-Bereich bearbeiten ======
class SectionEditView(discord.ui.View):
    """Kleines Unter-Menü, um genau einen Kanal/eine Rollen-Liste zu ändern."""

    def __init__(self, parent: BaseSetupView, key: str):
        super().__init__(timeout=900)
        self.parent = parent
        self.key = key
        self.name, self.kind, self.emoji, self.info = SECTIONS[key]

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await self.parent.interaction_check(interaction)

    def build_embed(self) -> discord.Embed:
        cfg = self.parent.cfg
        if self.kind == "channel":
            current = utils.mention_channel(self.parent.guild, cfg.get(self.key))
        else:
            current = utils.mention_roles(self.parent.guild, cfg.get(self.key))
        embed = discord.Embed(
            title=f"{self.emoji} {self.name}",
            description=f"{self.info}\n\n**Aktuell:** {current}",
            color=discord.Color.blurple(),
        )
        if self.kind == "roles":
            embed.set_footer(text="Mehrfachauswahl möglich • nichts auswählen = leeren")
        return embed

    def rebuild(self) -> None:
        self.clear_items()
        self.parent.cfg = self.parent.cfg or {}
        if self.kind == "channel":
            self.add_item(self.parent.make_channel_select(
                self.key, f"{self.name} auswählen…", row=0, after=self.refresh
            ))
        else:
            self.add_item(self.parent.make_role_select(
                self.key, f"{self.name} auswählen…", row=0, after=self.refresh
            ))

        if self.kind == "roles":
            clear = discord.ui.Button(label="Leeren", style=discord.ButtonStyle.danger,
                                      emoji="🧹", row=1)
            clear.callback = self._on_clear  # type: ignore[assignment]
            self.add_item(clear)

        back = discord.ui.Button(label="Zurück", style=discord.ButtonStyle.secondary,
                                 emoji="⬅️", row=1)
        back.callback = self._on_back  # type: ignore[assignment]
        self.add_item(back)

    async def refresh(self, interaction: discord.Interaction) -> None:
        self.parent.cfg = await self.parent.bot.get_cfg(self.parent.guild_id)
        self.rebuild()
        await self.parent.edit(interaction, content=None, embed=self.build_embed(), view=self)

    async def _on_clear(self, interaction: discord.Interaction) -> None:
        await self.parent.save(**{self.key: []})
        await self.refresh(interaction)

    async def _on_back(self, interaction: discord.Interaction) -> None:
        self.stop()
        await self.parent.render(interaction)


# ==================================================== Verwaltungs-Menü =======
class SetupPanelView(BaseSetupView):
    """Wird angezeigt, wenn das Setup bereits abgeschlossen ist."""

    async def build_embed(self) -> discord.Embed:
        cfg = self.cfg
        guild = self.guild
        fields = await self.bot.db.get_form_fields(self.guild_id)
        boxes = await self.bot.db.get_checkboxes(self.guild_id)

        embed = discord.Embed(
            title="⚙️ Verify-System – Verwaltung",
            description=(
                "Das Setup ist **abgeschlossen**. Hier kannst du alles einzeln ändern:\n"
                "wähle oben einen Bereich aus oder nutze die Buttons für Embed, "
                "Formular und Checkboxen."
            ),
            color=discord.Color.green() if cfg.get("setup_completed") else discord.Color.orange(),
        )
        embed.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)

        embed.add_field(name="📢 Verify-Kanal",
                        value=utils.mention_channel(guild, cfg.get("panel_channel_id")), inline=True)
        embed.add_field(name="📥 Anfragen-Kanal",
                        value=utils.mention_channel(guild, cfg.get("requests_channel_id")), inline=True)
        embed.add_field(name="🛡️ Team-Rollen",
                        value=utils.mention_roles(guild, cfg.get("staff_role_ids")), inline=False)
        embed.add_field(name="➕ Rollen hinzufügen",
                        value=utils.mention_roles(guild, cfg.get("add_role_ids")), inline=True)
        embed.add_field(name="➖ Rollen entfernen",
                        value=utils.mention_roles(guild, cfg.get("remove_role_ids")), inline=True)
        embed.add_field(name="🚪 Auto-Rollen beim Join",
                        value=utils.mention_roles(guild, cfg.get("autorole_ids")), inline=False)
        embed.add_field(
            name=f"📝 Formular ({len(fields)})",
            value=utils.truncate("\n".join(f"• {f['label']}" for f in fields) or "*keine*", 1024),
            inline=True,
        )
        embed.add_field(
            name=f"☑️ Checkboxen ({len(boxes)})",
            value=utils.truncate("\n".join(f"• {b['label']}" for b in boxes) or "*keine*", 1024),
            inline=True,
        )
        embed.add_field(
            name="🎨 Embed & Button",
            value=(
                f"**{utils.truncate(str(cfg.get('embed_title') or ''), 60)}**\n"
                f"Button: {cfg.get('button_emoji') or ''} `{cfg.get('button_label')}` "
                f"({cfg.get('button_style')})"
            ),
            inline=False,
        )
        embed.add_field(name="⚡ Auto-Annahme",
                        value="an" if cfg.get("auto_approve") else "aus", inline=True)
        embed.add_field(name="✉️ DM an Nutzer",
                        value="an" if cfg.get("dm_user", True) else "aus", inline=True)

        missing = self.missing_required()
        if missing:
            embed.add_field(name=f"{utils.WARN} Fehlt noch",
                            value="\n".join(f"• {m}" for m in missing), inline=False)
        warnings = self.permission_warnings()
        if warnings:
            embed.add_field(
                name=f"{utils.WARN} Fehlende Bot-Rechte",
                value="\n".join(f"• {w}" for w in warnings)
                      + "\n*Solange das so ist, kann ich dort nichts posten.*",
                inline=False,
            )
        embed.set_footer(text="Änderungen werden sofort gespeichert.")
        return embed

    def missing_required(self) -> list[str]:
        missing = []
        if not self.cfg.get("panel_channel_id"):
            missing.append("Verify-Kanal")
        if not self.cfg.get("requests_channel_id") and not self.cfg.get("auto_approve"):
            missing.append("Anfragen-Kanal")
        return missing

    def rebuild(self) -> None:
        self.clear_items()

        select = discord.ui.Select(
            placeholder="Bereich bearbeiten: Kanäle & Rollen…",
            options=[
                discord.SelectOption(label=name, value=key, emoji=emoji, description=utils.truncate(info, 90))
                for key, (name, _kind, emoji, info) in SECTIONS.items()
            ],
            row=0,
        )

        async def on_section(interaction: discord.Interaction) -> None:
            key = interaction.data["values"][0]  # type: ignore[index]
            view = SectionEditView(self, key)
            await view.refresh(interaction)

        select.callback = on_section  # type: ignore[assignment]
        self.add_item(select)

        b_embed = discord.ui.Button(label="Embed bearbeiten", style=discord.ButtonStyle.primary,
                                    emoji="🎨", row=1)
        b_embed.callback = self._edit_embed  # type: ignore[assignment]
        self.add_item(b_embed)

        b_button = discord.ui.Button(label="Button bearbeiten", style=discord.ButtonStyle.primary,
                                     emoji="🔘", row=1)
        b_button.callback = self._edit_button  # type: ignore[assignment]
        self.add_item(b_button)

        b_prev = discord.ui.Button(label="Vorschau", style=discord.ButtonStyle.secondary,
                                   emoji="👁️", row=1)
        b_prev.callback = self._preview  # type: ignore[assignment]
        self.add_item(b_prev)

        b_form = discord.ui.Button(label="Formular bearbeiten", style=discord.ButtonStyle.primary,
                                   emoji="📝", row=2)
        b_form.callback = self._open_forms  # type: ignore[assignment]
        self.add_item(b_form)

        b_check = discord.ui.Button(label="Checkboxen bearbeiten", style=discord.ButtonStyle.primary,
                                    emoji="☑️", row=2)
        b_check.callback = self._open_checkboxes  # type: ignore[assignment]
        self.add_item(b_check)

        auto = bool(self.cfg.get("auto_approve"))
        b_auto = discord.ui.Button(
            label=f"Auto-Annahme: {'an' if auto else 'aus'}", emoji="⚡", row=2,
            style=discord.ButtonStyle.success if auto else discord.ButtonStyle.secondary,
        )
        b_auto.callback = self._toggle_auto  # type: ignore[assignment]
        self.add_item(b_auto)

        dm = bool(self.cfg.get("dm_user", True))
        b_dm = discord.ui.Button(
            label=f"DM an Nutzer: {'an' if dm else 'aus'}", emoji="✉️", row=2,
            style=discord.ButtonStyle.success if dm else discord.ButtonStyle.secondary,
        )
        b_dm.callback = self._toggle_dm  # type: ignore[assignment]
        self.add_item(b_dm)

        b_send = discord.ui.Button(
            label="Embed (neu) senden", style=discord.ButtonStyle.success, emoji="🚀", row=3,
            disabled=not self.cfg.get("panel_channel_id"),
        )
        b_send.callback = self.do_send_panel  # type: ignore[assignment]
        self.add_item(b_send)

        b_wizard = discord.ui.Button(label="Setup neu durchlaufen",
                                     style=discord.ButtonStyle.secondary, emoji="🔄", row=3)
        b_wizard.callback = self._restart_wizard  # type: ignore[assignment]
        self.add_item(b_wizard)

        b_close = discord.ui.Button(label="Schließen", style=discord.ButtonStyle.danger,
                                    emoji="✖️", row=3)
        b_close.callback = self._close  # type: ignore[assignment]
        self.add_item(b_close)

    # ------------------------------------------------------------ callbacks --
    async def _edit_embed(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(EmbedEditModal(self, self.cfg))

    async def _edit_button(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(ButtonEditModal(self, self.cfg))

    async def _preview(self, interaction: discord.Interaction) -> None:
        cfg = await self.bot.get_cfg(self.guild_id)
        await interaction.response.send_message(
            content="**So sieht es im Kanal aus:**",
            embed=utils.build_panel_embed(cfg, self.guild),
            view=VerifyPanelView(self.bot, cfg),
            ephemeral=True,
        )

    async def _open_forms(self, interaction: discord.Interaction) -> None:
        await FormManagerView(self).refresh(interaction)

    async def _open_checkboxes(self, interaction: discord.Interaction) -> None:
        await CheckboxManagerView(self).refresh(interaction)

    async def _toggle_auto(self, interaction: discord.Interaction) -> None:
        await self.save(auto_approve=not bool(self.cfg.get("auto_approve")))
        await self.render(interaction)

    async def _toggle_dm(self, interaction: discord.Interaction) -> None:
        await self.save(dm_user=not bool(self.cfg.get("dm_user", True)))
        await self.render(interaction)

    async def _restart_wizard(self, interaction: discord.Interaction) -> None:
        self.stop()
        wizard = SetupWizard(self.bot, self.guild, self.author_id, step=0)
        wizard.message = self.message
        await wizard.render(interaction)

    async def _close(self, interaction: discord.Interaction) -> None:
        self.stop()
        await self.edit(interaction, content="Setup geschlossen.", embed=None, view=None)


# ================================================================ Wizard =====
class SetupWizard(BaseSetupView):
    """Führt Schritt für Schritt durch die Erst-Einrichtung."""

    def __init__(self, bot, guild: discord.Guild, author_id: int, step: int = 0):
        super().__init__(bot, guild, author_id)
        self.step = step

    # -------------------------------------------------------------- embeds --
    async def build_embed(self) -> discord.Embed:
        cfg = self.cfg
        guild = self.guild
        step = self.step
        progress = " › ".join(f"**{t}**" if i == step else t for i, t in enumerate(STEP_TITLES))
        embed = discord.Embed(color=discord.Color.blurple())
        embed.set_author(name=f"Verify-Setup • {guild.name}",
                         icon_url=guild.icon.url if guild.icon else None)

        if step == 0:
            embed.title = "👋 Willkommen beim Verify-Setup"
            embed.description = (
                "Ich stelle dir jetzt ein paar Fragen. Nach jeder Frage wird deine "
                "Antwort **sofort gespeichert**.\n\n"
                "**Das richten wir ein:**\n"
                "1️⃣ In welchen Kanal kommt das Verify-Embed?\n"
                "2️⃣ Wohin sollen die Verifizierungs-Anfragen?\n"
                "3️⃣ Welche Rollen dürfen Anfragen bearbeiten?\n"
                "4️⃣ Welche Rollen bekommt ein verifizierter Nutzer?\n"
                "5️⃣ Welche Rollen werden ihm entzogen?\n"
                "6️⃣ Auto-Rollen beim Serverbeitritt (optional)\n"
                "7️⃣ Embed & Button gestalten\n"
                "8️⃣ Bewerbung: Formular & Checkboxen (optional)\n\n"
                "*Ist das Setup einmal fertig, öffnet `/setup` direkt das "
                "Verwaltungs-Menü zum Bearbeiten.*"
            )
        elif step == 1:
            embed.title = "1️⃣ In welchen Kanal soll das Verify-Embed?"
            embed.description = (
                "Wähle den Kanal, in dem die Nachricht mit dem **Verifizieren-Button** "
                "gepostet wird.\n\n"
                f"**Aktuell:** {utils.mention_channel(guild, cfg.get('panel_channel_id'))}"
            )
        elif step == 2:
            embed.title = "2️⃣ Wohin sollen die Verifizierungs-Anfragen?"
            embed.description = (
                "In diesem Kanal landen alle Anfragen mit **Annehmen/Ablehnen**-Buttons.\n"
                "Am besten ein Kanal, den nur das Team sehen kann.\n\n"
                f"**Aktuell:** {utils.mention_channel(guild, cfg.get('requests_channel_id'))}"
            )
        elif step == 3:
            embed.title = "3️⃣ Welche Rollen sind für die Verifizierung zuständig?"
            embed.description = (
                "Diese Rollen dürfen Anfragen **annehmen und ablehnen** und werden bei "
                "einer neuen Anfrage gepingt.\n"
                "*(Admins und der Server-Owner dürfen es immer.)*\n\n"
                f"**Aktuell:** {utils.mention_roles(guild, cfg.get('staff_role_ids'))}"
            )
        elif step == 4:
            embed.title = "4️⃣ Welche Rollen bekommt ein verifizierter Nutzer?"
            embed.description = (
                "Diese Rollen werden **vergeben**, sobald eine Anfrage angenommen wird.\n\n"
                f"**Aktuell:** {utils.mention_roles(guild, cfg.get('add_role_ids'))}"
            )
        elif step == 5:
            embed.title = "5️⃣ Welche Rollen sollen abgezogen werden?"
            embed.description = (
                "Diese Rollen werden bei der Verifizierung **entfernt** "
                "(z. B. eine „Unverifiziert“-Rolle).\n\n"
                f"**Aktuell:** {utils.mention_roles(guild, cfg.get('remove_role_ids'))}"
            )
        elif step == 6:
            embed.title = "6️⃣ Auto-Rollen beim Beitritt (optional)"
            embed.description = (
                "Diese Rollen bekommt **jeder neue Nutzer automatisch**, sobald er dem "
                "Server beitritt – z. B. eine „Unverifiziert“-Rolle.\n\n"
                f"**Aktuell:** {utils.mention_roles(guild, cfg.get('autorole_ids'))}"
            )
        elif step == 7:
            embed.title = "7️⃣ Embed & Button gestalten"
            embed.description = (
                "So sieht die Nachricht aus, die im Verify-Kanal gepostet wird.\n"
                "Klicke auf **Embed bearbeiten** oder **Button bearbeiten**."
            )
            preview = utils.build_panel_embed(cfg, guild)
            embed.add_field(
                name="Vorschau",
                value=f"**{preview.title}**\n{utils.truncate(preview.description or '', 900)}",
                inline=False,
            )
            embed.add_field(
                name="Button",
                value=f"{cfg.get('button_emoji') or ''} `{cfg.get('button_label')}` "
                      f"({cfg.get('button_style')})",
                inline=False,
            )
        elif step == 8:
            fields = await self.bot.db.get_form_fields(self.guild_id)
            boxes = await self.bot.db.get_checkboxes(self.guild_id)
            embed.title = "8️⃣ Bewerbung – Formular & Checkboxen (optional)"
            embed.description = (
                "Möchtest du, dass Nutzer beim Klick auf **Verifizieren** ein Formular "
                "ausfüllen und/oder Checkboxen bestätigen müssen?\n\n"
                "• **Formular** = Textfragen in einem Popup (max. 5)\n"
                "• **Checkboxen** = anklickbare Punkte, z. B. Regeln akzeptieren\n\n"
                "Wenn du **beides leer lässt**, wird die Anfrage direkt ohne Fragen "
                "abgeschickt."
            )
            embed.add_field(
                name=f"📝 Formular-Fragen ({len(fields)})",
                value="\n".join(f"• {f['label']}" for f in fields) or "*keine*", inline=False,
            )
            embed.add_field(
                name=f"☑️ Checkboxen ({len(boxes)})",
                value="\n".join(f"• {b['label']}" for b in boxes) or "*keine*", inline=False,
            )
            embed.add_field(
                name="⚙️ Automatisch annehmen",
                value=("**An** – Nutzer werden sofort verifiziert (keine Team-Prüfung)"
                       if cfg.get("auto_approve") else "**Aus** – das Team prüft jede Anfrage"),
                inline=False,
            )
        else:
            fields = await self.bot.db.get_form_fields(self.guild_id)
            boxes = await self.bot.db.get_checkboxes(self.guild_id)
            embed.title = "✅ Zusammenfassung"
            embed.description = (
                "Alles bereit! Klicke auf **🚀 Embed senden**, um die Verify-Nachricht "
                "im Kanal zu posten."
            )
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
            embed.add_field(name="Auto-Rollen beim Join",
                            value=utils.mention_roles(guild, cfg.get("autorole_ids")), inline=False)
            embed.add_field(name="Bewerbung",
                            value=f"{len(fields)} Formular-Frage(n), {len(boxes)} Checkbox(en)",
                            inline=True)
            embed.add_field(name="Auto-Annahme",
                            value="an" if cfg.get("auto_approve") else "aus", inline=True)
            missing = self.missing_required()
            if missing:
                embed.add_field(name=f"{utils.WARN} Fehlt noch",
                                value="\n".join(f"• {m}" for m in missing), inline=False)
            warnings = self.permission_warnings()
            if warnings:
                embed.add_field(
                    name=f"{utils.WARN} Fehlende Bot-Rechte",
                    value="\n".join(f"• {w}" for w in warnings), inline=False,
                )

        embed.set_footer(text=f"Schritt {step + 1}/{len(STEP_TITLES)}  •  {progress}"[:2048])
        return embed

    def missing_required(self) -> list[str]:
        missing = []
        if not self.cfg.get("panel_channel_id"):
            missing.append("Verify-Kanal (Schritt 1)")
        if not self.cfg.get("requests_channel_id") and not self.cfg.get("auto_approve"):
            missing.append("Anfragen-Kanal (Schritt 2)")
        return missing

    # ------------------------------------------------------------ ui build --
    def rebuild(self) -> None:
        self.clear_items()
        step = self.step

        async def advance(interaction: discord.Interaction) -> None:
            self.step = min(self.step + 1, LAST_STEP)
            await self.render(interaction)

        if step == 1:
            self.add_item(self.make_channel_select(
                "panel_channel_id", "Verify-Kanal wählen…", after=advance))
        elif step == 2:
            self.add_item(self.make_channel_select(
                "requests_channel_id", "Anfragen-Kanal wählen…", after=advance))
        elif step == 3:
            self.add_item(self.make_role_select("staff_role_ids", "Team-Rollen wählen…"))
        elif step == 4:
            self.add_item(self.make_role_select("add_role_ids", "Rollen, die vergeben werden…"))
        elif step == 5:
            self.add_item(self.make_role_select("remove_role_ids", "Rollen, die entfernt werden…"))
        elif step == 6:
            self.add_item(self.make_role_select("autorole_ids", "Auto-Rollen beim Beitritt…"))
        elif step == 7:
            b1 = discord.ui.Button(label="Embed bearbeiten", style=discord.ButtonStyle.primary,
                                   emoji="🎨", row=1)
            b1.callback = self._edit_embed  # type: ignore[assignment]
            self.add_item(b1)
            b2 = discord.ui.Button(label="Button bearbeiten", style=discord.ButtonStyle.primary,
                                   emoji="🔘", row=1)
            b2.callback = self._edit_button  # type: ignore[assignment]
            self.add_item(b2)
            b3 = discord.ui.Button(label="Vorschau", style=discord.ButtonStyle.secondary,
                                   emoji="👁️", row=1)
            b3.callback = self._preview  # type: ignore[assignment]
            self.add_item(b3)
        elif step == 8:
            b1 = discord.ui.Button(label="Formular verwalten", style=discord.ButtonStyle.primary,
                                   emoji="📝", row=1)
            b1.callback = self._open_forms  # type: ignore[assignment]
            self.add_item(b1)
            b2 = discord.ui.Button(label="Checkboxen verwalten", style=discord.ButtonStyle.primary,
                                   emoji="☑️", row=1)
            b2.callback = self._open_checkboxes  # type: ignore[assignment]
            self.add_item(b2)
            auto = bool(self.cfg.get("auto_approve"))
            b3 = discord.ui.Button(
                label=f"Auto-Annahme: {'an' if auto else 'aus'}", emoji="⚡", row=1,
                style=discord.ButtonStyle.success if auto else discord.ButtonStyle.secondary,
            )
            b3.callback = self._toggle_auto  # type: ignore[assignment]
            self.add_item(b3)
            dm = bool(self.cfg.get("dm_user", True))
            b4 = discord.ui.Button(
                label=f"DM an Nutzer: {'an' if dm else 'aus'}", emoji="✉️", row=2,
                style=discord.ButtonStyle.success if dm else discord.ButtonStyle.secondary,
            )
            b4.callback = self._toggle_dm  # type: ignore[assignment]
            self.add_item(b4)
        elif step == LAST_STEP:
            jump = discord.ui.Select(
                placeholder="Einzelnen Schritt nochmal bearbeiten…",
                options=[
                    discord.SelectOption(label=STEP_TITLES[i], value=str(i))
                    for i in range(1, LAST_STEP)
                ],
                row=0,
            )

            async def jump_cb(interaction: discord.Interaction) -> None:
                self.step = int(interaction.data["values"][0])  # type: ignore[index]
                await self.render(interaction)

            jump.callback = jump_cb  # type: ignore[assignment]
            self.add_item(jump)

            send = discord.ui.Button(
                label="Embed senden", style=discord.ButtonStyle.success, emoji="🚀", row=1,
                disabled=bool(self.missing_required()),
            )
            send.callback = self._send_panel  # type: ignore[assignment]
            self.add_item(send)

            panel = discord.ui.Button(label="Verwaltungs-Menü", style=discord.ButtonStyle.primary,
                                      emoji="⚙️", row=1)
            panel.callback = self._open_panel  # type: ignore[assignment]
            self.add_item(panel)

        nav_row = 3 if step in (7, 8) else 2
        if step > 0:
            back = discord.ui.Button(label="Zurück", style=discord.ButtonStyle.secondary,
                                     emoji="⬅️", row=nav_row)
            back.callback = self._back  # type: ignore[assignment]
            self.add_item(back)

        if step == 0:
            start = discord.ui.Button(label="Setup starten", style=discord.ButtonStyle.success,
                                      emoji="▶️", row=nav_row)
            start.callback = self._next  # type: ignore[assignment]
            self.add_item(start)
        elif step < LAST_STEP:
            nxt = discord.ui.Button(label="Weiter", style=discord.ButtonStyle.primary,
                                    emoji="➡️", row=nav_row)
            nxt.callback = self._next  # type: ignore[assignment]
            self.add_item(nxt)

        if 0 < step < LAST_STEP:
            skip = discord.ui.Button(label="Zur Übersicht", style=discord.ButtonStyle.secondary,
                                     emoji="⏭️", row=nav_row)
            skip.callback = self._to_end  # type: ignore[assignment]
            self.add_item(skip)

        close = discord.ui.Button(label="Schließen", style=discord.ButtonStyle.danger,
                                  emoji="✖️", row=nav_row)
        close.callback = self._close  # type: ignore[assignment]
        self.add_item(close)

    # ------------------------------------------------------------ callbacks --
    async def _next(self, interaction: discord.Interaction) -> None:
        self.step = min(self.step + 1, LAST_STEP)
        await self.render(interaction)

    async def _back(self, interaction: discord.Interaction) -> None:
        self.step = max(self.step - 1, 0)
        await self.render(interaction)

    async def _to_end(self, interaction: discord.Interaction) -> None:
        self.step = LAST_STEP
        await self.render(interaction)

    async def _close(self, interaction: discord.Interaction) -> None:
        self.stop()
        await self.edit(interaction, content="Setup geschlossen.", embed=None, view=None)

    async def _edit_embed(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(EmbedEditModal(self, self.cfg))

    async def _edit_button(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(ButtonEditModal(self, self.cfg))

    async def _preview(self, interaction: discord.Interaction) -> None:
        cfg = await self.bot.get_cfg(self.guild_id)
        await interaction.response.send_message(
            content="**So sieht es im Kanal aus:**",
            embed=utils.build_panel_embed(cfg, self.guild),
            view=VerifyPanelView(self.bot, cfg),
            ephemeral=True,
        )

    async def _toggle_auto(self, interaction: discord.Interaction) -> None:
        await self.save(auto_approve=not bool(self.cfg.get("auto_approve")))
        await self.render(interaction)

    async def _toggle_dm(self, interaction: discord.Interaction) -> None:
        await self.save(dm_user=not bool(self.cfg.get("dm_user", True)))
        await self.render(interaction)

    async def _open_forms(self, interaction: discord.Interaction) -> None:
        await FormManagerView(self).refresh(interaction)

    async def _open_checkboxes(self, interaction: discord.Interaction) -> None:
        await CheckboxManagerView(self).refresh(interaction)

    async def _open_panel(self, interaction: discord.Interaction) -> None:
        self.stop()
        panel = SetupPanelView(self.bot, self.guild, self.author_id)
        panel.message = self.message
        await panel.render(interaction)

    async def _send_panel(self, interaction: discord.Interaction) -> None:
        """Sendet das Embed und wechselt danach ins Verwaltungs-Menü."""
        if not interaction.response.is_done():
            await interaction.response.defer()
        try:
            cfg = await self.bot.get_cfg(self.guild_id)
            message, info = await send_verify_panel(self.bot, self.guild, cfg)
        except Exception as exc:  # noqa: BLE001
            log.exception("Panel senden fehlgeschlagen")
            message, info = None, f"{utils.NO} Unerwarteter Fehler: `{exc}`"

        if message is None:
            self.step = LAST_STEP
            await self.render(interaction)
            await interaction.followup.send(info, ephemeral=True)
            return

        self.stop()
        panel = SetupPanelView(self.bot, self.guild, self.author_id)
        panel.message = self.message
        await panel.render(interaction)
        await interaction.followup.send(info, ephemeral=True)
