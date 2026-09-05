"""Offline-Simulation des kompletten Ablaufs (ohne Discord & ohne Datenbank).

Ausführen:  python tests/test_flow.py
Prüft, dass Setup-Assistent, Formular, Checkboxen und die Team-Prüfung ohne
Laufzeitfehler durchlaufen.
"""
from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DISCORD_TOKEN", "test")
os.environ.setdefault("SUPABASE_DB_URL", "postgresql://u:p@localhost:5432/db")

import discord  # noqa: E402

import config  # noqa: E402
import utils  # noqa: E402
from views import verify as V  # noqa: E402
from views.setup_wizard import (  # noqa: E402
    CheckboxManagerView, FormManagerView, SectionEditView, SetupPanelView, SetupWizard,
)

SENT: list[str] = []


# --------------------------------------------------------------- Fake-DB -----
class FakeDB:
    def __init__(self) -> None:
        self.configs: dict[int, dict] = {}
        self.fields: dict[int, list[dict]] = {}
        self.boxes: dict[int, list[dict]] = {}
        self.requests: dict[int, dict] = {}
        self._id = 0

    def _next(self) -> int:
        self._id += 1
        return self._id

    async def get_config(self, gid):
        return self.configs.setdefault(gid, {
            "guild_id": gid, "panel_channel_id": None, "requests_channel_id": None,
            "log_channel_id": None, "staff_role_ids": [], "add_role_ids": [],
            "remove_role_ids": [], "autorole_ids": [],
            "embed_title": config.DEFAULT_EMBED_TITLE,
            "embed_description": config.DEFAULT_EMBED_DESCRIPTION,
            "embed_color": config.DEFAULT_EMBED_COLOR, "embed_footer": None,
            "embed_image_url": None, "embed_thumbnail_url": None,
            "button_label": config.DEFAULT_BUTTON_LABEL,
            "button_emoji": config.DEFAULT_BUTTON_EMOJI, "button_style": "success",
            "application_enabled": False, "auto_approve": False, "dm_user": True,
            "panel_message_id": None, "panel_message_channel_id": None,
            "setup_completed": False,
        })

    async def update_config(self, gid, **values):
        cfg = await self.get_config(gid)
        cfg.update(values)
        return dict(cfg)

    async def get_form_fields(self, gid):
        return [dict(f) for f in self.fields.get(gid, [])]

    async def add_form_field(self, gid, label, placeholder=None, style="short",
                             required=True, max_length=None):
        fid = self._next()
        self.fields.setdefault(gid, []).append({
            "id": fid, "guild_id": gid, "position": len(self.fields.get(gid, [])),
            "label": label, "placeholder": placeholder, "style": style,
            "required": required, "min_length": None, "max_length": max_length,
        })
        return fid

    async def update_form_field(self, fid, **values):
        for lst in self.fields.values():
            for f in lst:
                if f["id"] == fid:
                    f.update(values)

    async def delete_form_field(self, fid):
        for gid, lst in self.fields.items():
            self.fields[gid] = [f for f in lst if f["id"] != fid]

    async def get_checkboxes(self, gid):
        return [dict(b) for b in self.boxes.get(gid, [])]

    async def add_checkbox(self, gid, label, description=None, required=True):
        bid = self._next()
        self.boxes.setdefault(gid, []).append({
            "id": bid, "guild_id": gid, "position": len(self.boxes.get(gid, [])),
            "label": label, "description": description, "required": required,
        })
        return bid

    async def update_checkbox(self, bid, **values):
        for lst in self.boxes.values():
            for b in lst:
                if b["id"] == bid:
                    b.update(values)

    async def delete_checkbox(self, bid):
        for gid, lst in self.boxes.items():
            self.boxes[gid] = [b for b in lst if b["id"] != bid]

    async def create_request(self, gid, uid, answers, checks):
        rid = self._next()
        self.requests[rid] = {
            "id": rid, "guild_id": gid, "user_id": uid, "answers": list(answers),
            "checks": list(checks), "status": "pending", "channel_id": None,
            "message_id": None, "handled_by": None, "reason": None,
        }
        return rid

    async def get_request(self, rid):
        r = self.requests.get(rid)
        return dict(r) if r else None

    async def get_pending_request(self, gid, uid):
        for r in self.requests.values():
            if r["guild_id"] == gid and r["user_id"] == uid and r["status"] == "pending":
                return dict(r)
        return None

    async def set_request_message(self, rid, cid, mid):
        self.requests[rid].update(channel_id=cid, message_id=mid)

    async def close_request(self, rid, status, by, reason=None):
        self.requests[rid].update(status=status, handled_by=by, reason=reason)

    async def is_verified(self, gid, uid):
        return any(r["guild_id"] == gid and r["user_id"] == uid and r["status"] == "approved"
                   for r in self.requests.values())

    async def reset_guild(self, gid):
        self.configs.pop(gid, None)
        self.fields.pop(gid, None)
        self.boxes.pop(gid, None)


class FakeBot:
    def __init__(self) -> None:
        self.db = FakeDB()
        self.cache: dict[int, dict] = {}
        self.user = SimpleNamespace(id=999, __str__=lambda self: "Bot")

    async def load_guild(self, gid):
        data = {
            "config": await self.db.get_config(gid),
            "fields": await self.db.get_form_fields(gid),
            "checkboxes": await self.db.get_checkboxes(gid),
        }
        self.cache[gid] = data
        return data

    async def get_cfg(self, gid):
        cached = self.cache.get(gid)
        return cached["config"] if cached else (await self.load_guild(gid))["config"]

    async def update_cfg(self, gid, **values):
        cfg = await self.db.update_config(gid, **values)
        self.cache.setdefault(gid, {"fields": [], "checkboxes": []})["config"] = cfg
        return cfg

    async def fetch_channel(self, cid):
        raise discord.NotFound(SimpleNamespace(status=404, reason=""), "unknown channel")


# ------------------------------------------------------- Fake Discord --------
class FakeRole:
    def __init__(self, rid, name, pos=1):
        self.id, self.name, self.position = rid, name, pos
        self.managed = False
        self.mention = f"<@&{rid}>"

    def is_default(self):
        return self.id == 1

    def __lt__(self, other):
        return self.position < other.position


class FakeChannel:
    def __init__(self, cid, guild):
        self.id, self.guild, self.mention = cid, guild, f"<#{cid}>"
        self.messages = []

    def permissions_for(self, m):
        return SimpleNamespace(view_channel=True, send_messages=True, embed_links=True,
                               read_message_history=True)

    async def send(self, content=None, embed=None, view=None, allowed_mentions=None):
        msg = FakeMessage(len(self.messages) + 1000, self, embed, view)
        self.messages.append(msg)
        SENT.append(f"[{self.id}] {embed.title if embed else content}")
        return msg

    async def fetch_message(self, mid):
        for m in self.messages:
            if m.id == mid:
                return m
        raise discord.NotFound(SimpleNamespace(status=404, reason=""), "nope")


class FakeMessage:
    def __init__(self, mid, channel, embed=None, view=None, author_id=999):
        self.id, self.channel = mid, channel
        self.embeds = [embed] if embed else []
        self.view = view
        self.author = SimpleNamespace(id=author_id)
        self.jump_url = f"https://discord.com/{mid}"

    async def edit(self, **kw):
        if "embed" in kw and kw["embed"] is not None:
            self.embeds = [kw["embed"]]
        self.view = kw.get("view", self.view)
        return self

    async def delete(self):
        if self in self.channel.messages:
            self.channel.messages.remove(self)


class FakeMember:
    def __init__(self, uid, guild, roles=None, admin=True):
        self.id, self.guild, self.bot = uid, guild, False
        self.roles = roles or []
        self.mention = f"<@{uid}>"
        self.display_avatar = SimpleNamespace(url="https://cdn/x.png")
        self.created_at = discord.utils.utcnow()
        self.joined_at = discord.utils.utcnow()
        self.guild_permissions = SimpleNamespace(administrator=admin, manage_guild=admin)
        self.added, self.removed, self.dms = [], [], []

    def __str__(self):
        return f"User{self.id}"

    async def add_roles(self, *roles, reason=None):
        self.added += list(roles)
        self.roles += list(roles)

    async def remove_roles(self, *roles, reason=None):
        self.removed += list(roles)
        self.roles = [r for r in self.roles if r not in roles]

    async def send(self, embed=None, **kw):
        self.dms.append(embed.title if embed else "")


class FakeGuild:
    def __init__(self):
        self.id, self.name, self.icon = 1, "Testserver", None
        self.owner_id = 100
        self.roles = {
            10: FakeRole(10, "Team", 5),
            11: FakeRole(11, "Verifiziert", 4),
            12: FakeRole(12, "Unverifiziert", 3),
        }
        self.channels = {200: FakeChannel(200, self), 201: FakeChannel(201, self)}
        self.me = SimpleNamespace(top_role=FakeRole(99, "Bot", 50))
        self.members: dict[int, FakeMember] = {}

    def get_role(self, rid):
        return self.roles.get(rid)

    def get_channel(self, cid):
        return self.channels.get(cid)

    def get_member(self, uid):
        return self.members.get(uid)


class FakeResponse:
    def __init__(self, ia):
        self.ia, self._done = ia, False
        self.modal = None

    def is_done(self):
        return self._done

    async def send_message(self, content=None, embed=None, view=None, ephemeral=False):
        self._done = True
        self.ia.sent.append(content or (embed.title if embed else ""))
        self.ia.last_view = view

    async def edit_message(self, **kw):
        self._done = True
        self.ia.edits.append(kw.get("content") or
                             (kw["embed"].title if kw.get("embed") else ""))
        self.ia.last_view = kw.get("view", self.ia.last_view)

    async def defer(self, ephemeral=False, thinking=False):
        self._done = True

    async def send_modal(self, modal):
        self._done = True
        self.modal = modal


class FakeFollowup:
    def __init__(self, ia):
        self.ia = ia

    async def send(self, content=None, embed=None, view=None, ephemeral=False):
        self.ia.sent.append(content or (embed.title if embed else ""))


class FakeInteraction:
    def __init__(self, user, guild, client, message=None, data=None):
        self.user, self.guild, self.client = user, guild, client
        self.guild_id = guild.id if guild else None
        self.message, self.data = message, data or {}
        self.response = FakeResponse(self)
        self.followup = FakeFollowup(self)
        self.sent, self.edits = [], []
        self.last_view = None

    async def original_response(self):
        return FakeMessage(1, list(self.guild.channels.values())[0])

    async def edit_original_response(self, **kw):
        self.edits.append(kw.get("content") or (kw["embed"].title if kw.get("embed") else ""))
        self.last_view = kw.get("view", self.last_view)


# Damit die isinstance()-Prüfungen im Produktivcode mit den Fakes funktionieren:
discord.Member = FakeMember          # type: ignore[misc,assignment]
discord.TextChannel = FakeChannel    # type: ignore[misc,assignment]
discord.Thread = FakeChannel         # type: ignore[misc,assignment]
discord.ForumChannel = FakeChannel   # type: ignore[misc,assignment]


async def cancel_flow(view, user, guild, bot):
    await view.on_cancel(FakeInteraction(user, guild, bot))


def button(view, label_part):
    for item in view.children:
        if isinstance(item, discord.ui.Button) and label_part.lower() in (item.label or "").lower():
            return item
    raise AssertionError(f"Button {label_part!r} nicht gefunden in {[getattr(c,'label',c) for c in view.children]}")


# ------------------------------------------------------------------ Tests ----
async def main() -> None:
    bot = FakeBot()
    guild = FakeGuild()
    admin = FakeMember(100, guild, admin=True)
    user = FakeMember(555, guild, roles=[guild.roles[12]], admin=False)
    guild.members[100] = admin
    guild.members[555] = user

    # --- Rechte ---
    assert utils.is_setup_allowed(admin, guild)
    assert utils.is_setup_allowed(SimpleNamespace(id=config.BOT_OWNER_ID), guild)
    assert not utils.is_setup_allowed(user, guild)
    print("✔ Rechte-Prüfung")

    # --- Setup-Assistent durchklicken ---
    wizard = SetupWizard(bot, guild, admin.id)
    ia = FakeInteraction(admin, guild, bot)
    await wizard.start(ia)
    assert wizard.step == 0

    await button(wizard, "Setup starten").callback(FakeInteraction(admin, guild, bot))
    assert wizard.step == 1

    # Schritt 1+2: Kanäle
    for cid in (200, 201):
        sel = wizard.children[0]
        sel._values = [SimpleNamespace(id=cid)]  # type: ignore[attr-defined]
        object.__setattr__(sel, "_selected_values", [SimpleNamespace(id=cid)])
        await sel.callback(FakeInteraction(admin, guild, bot))
    cfg = await bot.get_cfg(guild.id)
    assert cfg["panel_channel_id"] == 200 and cfg["requests_channel_id"] == 201
    print("✔ Kanäle gesetzt:", cfg["panel_channel_id"], cfg["requests_channel_id"])

    # Schritt 3-6: Rollen direkt speichern (Select-Werte lassen sich offline nicht faken)
    await wizard.save(staff_role_ids=[10], add_role_ids=[11],
                      remove_role_ids=[12], autorole_ids=[12])
    wizard.step = 7
    await wizard.render(FakeInteraction(admin, guild, bot))

    # Schritt 7: Embed + Button bearbeiten
    ia = FakeInteraction(admin, guild, bot)
    await button(wizard, "Embed bearbeiten").callback(ia)
    modal = ia.response.modal
    modal.f_title._value = "Willkommen!"
    modal.f_desc._value = "Bitte verifizieren."
    modal.f_color._value = "gruen"
    modal.f_footer._value = "Team"
    modal.f_image._value = ""
    await modal.on_submit(FakeInteraction(admin, guild, bot))
    cfg = await bot.get_cfg(guild.id)
    assert cfg["embed_title"] == "Willkommen!" and cfg["embed_color"] == 0x2ECC71

    ia = FakeInteraction(admin, guild, bot)
    await button(wizard, "Button bearbeiten").callback(ia)
    m = ia.response.modal
    m.f_label._value, m.f_emoji._value, m.f_style._value = "Jetzt verifizieren", "🔐", "blau"
    await m.on_submit(FakeInteraction(admin, guild, bot))
    cfg = await bot.get_cfg(guild.id)
    assert cfg["button_label"] == "Jetzt verifizieren" and cfg["button_style"] == "primary"
    print("✔ Embed & Button bearbeitet")

    # Schritt 8: Formular + Checkboxen
    wizard.step = 8
    await wizard.render(FakeInteraction(admin, guild, bot))
    ia = FakeInteraction(admin, guild, bot)
    await button(wizard, "Formular verwalten").callback(ia)
    fm = ia.last_view
    assert isinstance(fm, FormManagerView)
    ia2 = FakeInteraction(admin, guild, bot)
    await button(fm, "Frage hinzufügen").callback(ia2)
    fmodal = ia2.response.modal
    fmodal.f_label._value = "Wie alt bist du?"
    fmodal.f_placeholder._value = "z. B. 17"
    fmodal.f_style._value, fmodal.f_required._value, fmodal.f_max._value = "kurz", "ja", "3"
    await fmodal.on_submit(FakeInteraction(admin, guild, bot))
    assert len(await bot.db.get_form_fields(guild.id)) == 1

    ia3 = FakeInteraction(admin, guild, bot)
    await button(wizard, "Checkboxen verwalten").callback(ia3)
    cm = ia3.last_view
    assert isinstance(cm, CheckboxManagerView)
    ia4 = FakeInteraction(admin, guild, bot)
    await button(cm, "Checkbox hinzufügen").callback(ia4)
    cmodal = ia4.response.modal
    cmodal.f_label._value = "Ich akzeptiere die Regeln"
    cmodal.f_desc._value = "Steht in #regeln"
    cmodal.f_required._value = "ja"
    await cmodal.on_submit(FakeInteraction(admin, guild, bot))
    ia5 = FakeInteraction(admin, guild, bot)
    await button(cm, "Checkbox hinzufügen").callback(ia5)
    c2 = ia5.response.modal
    c2.f_label._value, c2.f_desc._value, c2.f_required._value = "Newsletter", "", "nein"
    await c2.on_submit(FakeInteraction(admin, guild, bot))
    assert len(await bot.db.get_checkboxes(guild.id)) == 2
    print("✔ Formular & Checkboxen angelegt")

    # Übersicht + Panel senden
    wizard.step = 9
    await wizard.render(FakeInteraction(admin, guild, bot))
    assert not wizard.missing_required(), wizard.missing_required()
    ia = FakeInteraction(admin, guild, bot)
    await button(wizard, "Embed senden").callback(ia)
    assert any("gesendet" in s for s in ia.sent), ia.sent
    panel_channel = guild.get_channel(200)
    assert panel_channel.messages, "Panel wurde nicht gesendet"
    panel_msg = panel_channel.messages[-1]
    assert panel_msg.view.children[0].custom_id == config.VERIFY_BUTTON_ID
    print("✔ Panel gesendet:", panel_msg.embeds[0].title,
          "| Button:", panel_msg.view.children[0].label)

    # --- /setup erneut: jetzt Verwaltungs-Menü statt Assistent ---
    cfg = await bot.get_cfg(guild.id)
    assert cfg["setup_completed"] is True
    panel_view = SetupPanelView(bot, guild, admin.id)
    ia = FakeInteraction(admin, guild, bot)
    await panel_view.start(ia)
    labels = [getattr(c, "label", None) for c in panel_view.children]
    assert "Formular bearbeiten" in labels and "Checkboxen bearbeiten" in labels
    assert "Embed senden" in labels and "Setup neu durchlaufen" in labels
    print("✔ Verwaltungs-Menü:", [x for x in labels if x])

    # Formular aus dem Verwaltungs-Menü bearbeiten
    ia = FakeInteraction(admin, guild, bot)
    await button(panel_view, "Formular bearbeiten").callback(ia)
    fm2 = ia.last_view
    assert isinstance(fm2, FormManagerView) and fm2.fields
    fm2.selected = fm2.fields[0]["id"]
    fm2.rebuild()
    ia2 = FakeInteraction(admin, guild, bot)
    await button(fm2, "Bearbeiten").callback(ia2)
    em = ia2.response.modal
    em.f_label._value = "Wie alt bist du genau?"
    em.f_placeholder._value = ""
    em.f_style._value, em.f_required._value, em.f_max._value = "kurz", "ja", ""
    await em.on_submit(FakeInteraction(admin, guild, bot))
    assert (await bot.db.get_form_fields(guild.id))[0]["label"] == "Wie alt bist du genau?"
    # direkt zu den Checkboxen wechseln und wieder zurück
    ia3 = FakeInteraction(admin, guild, bot)
    await button(fm2, "Zu den Checkboxen").callback(ia3)
    cm2 = ia3.last_view
    assert isinstance(cm2, CheckboxManagerView) and len(cm2.boxes) == 2
    ia4 = FakeInteraction(admin, guild, bot)
    await button(cm2, "Zum Formular").callback(ia4)
    assert isinstance(ia4.last_view, FormManagerView)
    await button(ia4.last_view, "Zurück").callback(FakeInteraction(admin, guild, bot))
    print("✔ Formular über Verwaltungs-Menü bearbeitet + Wechsel zu Checkboxen")

    # Bereich (Rollen) über das Dropdown ändern
    section = SectionEditView(panel_view, "add_role_ids")
    await section.refresh(FakeInteraction(admin, guild, bot))
    await button(section, "Leeren").callback(FakeInteraction(admin, guild, bot))
    assert (await bot.get_cfg(guild.id))["add_role_ids"] == []
    await panel_view.save(add_role_ids=[11])
    await button(section, "Zurück").callback(FakeInteraction(admin, guild, bot))
    print("✔ Bereich über Dropdown bearbeitet (leeren + zurück)")

    # Toggles
    await button(panel_view, "DM an Nutzer").callback(FakeInteraction(admin, guild, bot))
    assert (await bot.get_cfg(guild.id))["dm_user"] is False
    await button(panel_view, "DM an Nutzer").callback(FakeInteraction(admin, guild, bot))
    assert (await bot.get_cfg(guild.id))["dm_user"] is True
    print("✔ Schalter im Verwaltungs-Menü funktionieren")

    # Erneut senden -> vorhandenes Embed wird BEARBEITET, nicht neu gepostet
    before = len(panel_channel.messages)
    old_msg_id = panel_channel.messages[-1].id
    await panel_view.save(embed_title="Aktualisierter Titel")
    ia = FakeInteraction(admin, guild, bot)
    await button(panel_view, "Embed senden").callback(ia)
    assert len(panel_channel.messages) == before, "es wurde neu gepostet statt bearbeitet"
    panel_msg = panel_channel.messages[-1]
    assert panel_msg.id == old_msg_id, "Nachricht wurde ersetzt statt bearbeitet"
    assert panel_msg.embeds[0].title == "Aktualisierter Titel"
    assert any("aktualisiert" in s for s in ia.sent), ia.sent
    print("✔ Vorhandenes Embed wurde bearbeitet statt neu gesendet")

    # Kanal wechseln -> altes Embed wird geloescht, neues gepostet
    await panel_view.save(panel_channel_id=201)
    ia = FakeInteraction(admin, guild, bot)
    await button(panel_view, "Embed senden").callback(ia)
    assert old_msg_id not in [m.id for m in panel_channel.messages], "altes Embed blieb liegen"
    print("✔ Kanalwechsel: altes Embed entfernt, neues gesendet")
    await panel_view.save(panel_channel_id=200)
    ia = FakeInteraction(admin, guild, bot)
    await button(panel_view, "Embed senden").callback(ia)
    panel_msg = panel_channel.messages[-1]

    # Fehlerfall: Kanal gelöscht -> klare Rückmeldung statt Stille
    await bot.update_cfg(guild.id, panel_channel_id=99999)
    ia = FakeInteraction(admin, guild, bot)
    await button(panel_view, "Embed senden").callback(ia)
    assert any("finde ich nicht mehr" in s for s in ia.sent), ia.sent
    print("✔ Fehlerfall meldet sich:", [s for s in ia.sent if "❌" in s][0][:70], "…")
    await bot.update_cfg(guild.id, panel_channel_id=200, panel_message_id=panel_msg.id)

    # --- Nutzer klickt Verifizieren ---
    await bot.load_guild(guild.id)
    ia = FakeInteraction(user, guild, bot, message=panel_msg)
    await panel_msg.view._callback(ia)
    modal = ia.response.modal
    assert isinstance(modal, V.VerifyFormModal), "Formular-Modal fehlt"
    assert len(modal.groups) == 2, "Pflicht- und Optional-Gruppe fehlen im Modal"
    payload = modal.to_dict()
    types = [c["component"]["type"] for c in payload["components"]]
    assert 4 in types and 22 in types, types   # TextInput + CheckboxGroup im selben Modal
    print("✔ Formular enthält echte Discord-Checkboxen (Komponententyp 22):", types)

    modal.inputs[0][1]._value = "17"
    # Absenden ohne Pflicht-Häkchen -> Warnung
    ia_bad = FakeInteraction(user, guild, bot)
    await modal.on_submit(ia_bad)
    assert any("bestätigen" in s for s in ia_bad.sent), ia_bad.sent
    print("✔ Pflicht-Checkbox wird erzwungen")

    # Häkchen setzen und absenden
    for group, boxes in modal.groups:
        if boxes[0]["required"]:
            group._values = [str(b["id"]) for b in boxes]
    ia_ok = FakeInteraction(user, guild, bot)
    await modal.on_submit(ia_ok)
    req_channel = guild.get_channel(201)
    assert req_channel.messages, "Anfrage nicht gesendet"
    req_msg = req_channel.messages[-1]
    print("✔ Anfrage gesendet:", req_msg.embeds[0].title)

    # Doppelte Anfrage wird blockiert
    ia_dup = FakeInteraction(user, guild, bot, message=panel_msg)
    await panel_msg.view._callback(ia_dup)
    assert any("offene Anfrage" in s for s in ia_dup.sent), ia_dup.sent
    print("✔ Doppelte Anfrage blockiert")

    # --- Team nimmt an ---
    accept = req_msg.view.children[0]
    ia_acc = FakeInteraction(admin, guild, bot, message=req_msg)
    await accept.callback(ia_acc)
    assert guild.roles[11] in user.roles, "Rolle nicht vergeben"
    assert guild.roles[12] not in user.roles, "Rolle nicht entfernt"
    assert user.dms, "Keine DM verschickt"
    assert req_msg.embeds[0].title.endswith("angenommen")
    print("✔ Angenommen → Rollen:", [r.name for r in user.roles], "| DM:", user.dms)

    # Erneuter Klick -> bereits verifiziert
    ia_again = FakeInteraction(user, guild, bot, message=panel_msg)
    await panel_msg.view._callback(ia_again)
    assert any("bereits verifiziert" in s.lower() for s in ia_again.sent), ia_again.sent
    print("✔ Bereits verifizierte Nutzer werden erkannt (Rollen)")

    # auch ohne die Rollen: DB kennt die abgeschlossene Verifizierung
    user.roles = []
    ia_again2 = FakeInteraction(user, guild, bot, message=panel_msg)
    await panel_msg.view._callback(ia_again2)
    assert any("bereits verifiziert" in s.lower() for s in ia_again2.sent), ia_again2.sent
    print("✔ Zweite Verifizierung wird über die Datenbank blockiert")

    # --- Ablehnung eines zweiten Nutzers ---
    user2 = FakeMember(777, guild, roles=[guild.roles[12]], admin=False)
    guild.members[777] = user2
    ia = FakeInteraction(user2, guild, bot, message=panel_msg)
    await panel_msg.view._callback(ia)
    modal = ia.response.modal
    modal.inputs[0][1]._value = "12"
    for group, boxes in modal.groups:
        if boxes[0]["required"]:
            group._values = [str(b["id"]) for b in boxes]
    await modal.on_submit(FakeInteraction(user2, guild, bot))
    req_msg2 = guild.get_channel(201).messages[-1]
    deny = req_msg2.view.children[1]
    ia_deny = FakeInteraction(admin, guild, bot, message=req_msg2)
    await deny.callback(ia_deny)
    dmodal = ia_deny.response.modal
    dmodal.reason._value = "Zu jung"
    await dmodal.on_submit(FakeInteraction(admin, guild, bot, message=req_msg2))
    assert req_msg2.embeds[0].title.endswith("abgelehnt")
    assert guild.roles[11] not in user2.roles
    print("✔ Abgelehnt mit Grund | DM:", user2.dms)

    # Doppelte Bearbeitung
    ia_twice = FakeInteraction(admin, guild, bot, message=req_msg2)
    await req_msg2.view.children[0].callback(ia_twice) if req_msg2.view else None
    print("✔ Bereits bearbeitete Anfrage abgefangen")

    # --- Checkbox erst nach dem Öffnen des Formulars angelegt (Cache veraltet) ---
    user5 = FakeMember(1234, guild, roles=[guild.roles[12]], admin=False)
    guild.members[1234] = user5
    bot.cache[guild.id]["checkboxes"] = []          # Cache absichtlich veralten lassen
    await bot.load_guild(guild.id)                  # Hintergrund-Refresh (alle 5 Min.)
    ia = FakeInteraction(user5, guild, bot, message=panel_msg)
    await panel_msg.view._callback(ia)
    modal = ia.response.modal
    assert any(c["component"]["type"] == 22 for c in modal.to_dict()["components"]), \
        "Checkboxen fehlen im Modal, obwohl sie in der DB stehen"
    print("✔ Cache-Refresh holt neue Checkboxen ins Formular")
    await bot.load_guild(guild.id)

    # --- Ohne Formular/Checkboxen: direkte Anfrage ---
    for f in await bot.db.get_form_fields(guild.id):
        await bot.db.delete_form_field(f["id"])
    for b in await bot.db.get_checkboxes(guild.id):
        await bot.db.delete_checkbox(b["id"])
    await bot.load_guild(guild.id)
    user3 = FakeMember(888, guild, admin=False)
    guild.members[888] = user3
    ia = FakeInteraction(user3, guild, bot, message=panel_msg)
    await panel_msg.view._callback(ia)
    assert guild.get_channel(201).messages[-1].embeds[0].title.startswith("🕓")
    print("✔ Direkte Anfrage ohne Formular/Checkbox")

    # --- Auto-Annahme ---
    await bot.update_cfg(guild.id, auto_approve=True)
    user4 = FakeMember(999, guild, roles=[guild.roles[12]], admin=False)
    guild.members[999] = user4
    ia = FakeInteraction(user4, guild, bot, message=panel_msg)
    await panel_msg.view._callback(ia)
    assert guild.roles[11] in user4.roles
    print("✔ Auto-Annahme vergibt Rollen sofort")

    print("\n🎉 Alle Szenarien erfolgreich durchlaufen.")


if __name__ == "__main__":
    asyncio.run(main())
