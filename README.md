# 🔐 Verify-System (Discord Bot)

Ein komplettes Verifizierungs-System für Discord: `/setup`-Assistent, anpassbares
Embed mit **Verifizieren**-Button, optionales Bewerbungs-Formular + Checkboxen,
Anfragen-Kanal mit **Annehmen / Ablehnen**, Rollenvergabe und Auto-Rollen beim Join.

Datenbank: **Supabase (PostgreSQL)** · Hosting: **Render** · Sprache: **Python (discord.py)**

---

## ⚡ TL;DR – Render

| Feld | Wert |
|------|------|
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `python bot.py` |
| **Service-Typ** | `Background Worker` (empfohlen) oder `Web Service` (free) |

**Environment Variables:**

| Name | Pflicht | Beispiel / Wert |
|------|---------|-----------------|
| `DISCORD_TOKEN` | ✅ | Bot-Token aus dem Discord Developer Portal |
| `SUPABASE_DB_URL` | ✅ | `postgresql://postgres.abcdef:PASSWORT@aws-0-eu-central-1.pooler.supabase.com:6543/postgres` |
| `BOT_OWNER_ID` | ➖ | `1313187996790427688` (Standard, kann `/setup` immer nutzen) |
| `PYTHON_VERSION` | ➖ | `3.11.9` |
| `DEV_GUILD_ID` | ➖ | Test-Server-ID – Slash-Commands erscheinen dort **sofort** |
| `PORT` | ➖ | setzt Render bei Web Services automatisch – der Bot öffnet dann einen Health-Endpoint |

> `DATABASE_URL` funktioniert alternativ zu `SUPABASE_DB_URL`.

---

## 1. Discord-Bot anlegen

1. <https://discord.com/developers/applications> → **New Application**
2. **Bot** → *Reset Token* → Token kopieren (= `DISCORD_TOKEN`)
3. Bei **Privileged Gateway Intents** einschalten:
   * ✅ **SERVER MEMBERS INTENT** (zwingend – für Rollen & Auto-Rollen)
4. **OAuth2 → URL Generator**:
   * Scopes: `bot`, `applications.commands`
   * Bot Permissions: `Manage Roles`, `Send Messages`, `Embed Links`,
     `Read Message History`, `View Channels`
5. Einladen. **Wichtig:** Die Bot-Rolle muss in den Server-Einstellungen
   **über** allen Rollen stehen, die sie vergeben/entfernen soll.

## 2. Supabase-Datenbank

1. <https://supabase.com> → Projekt erstellen
2. **Project Settings → Database → Connection string → URI**
3. Für Render den **Connection Pooler** (Port `6543`) nehmen und `[YOUR-PASSWORD]`
   durch dein DB-Passwort ersetzen → das ist `SUPABASE_DB_URL`
4. Tabellen musst du **nicht** anlegen – der Bot erstellt sie beim ersten Start
   automatisch (`guild_configs`, `form_fields`, `checkboxes`, `verification_requests`).

## 3. Auf Render deployen

1. Repo auf GitHub pushen → Render → **New +** → **Background Worker**
   (oder **Web Service**, wenn du den Free-Plan nutzen willst)
2. Repo auswählen, Runtime **Python 3**
3. **Build Command:** `pip install -r requirements.txt`
4. **Start Command:** `python bot.py`
5. Die Environment Variables aus der Tabelle oben eintragen → **Deploy**

Im Repo liegt außerdem eine `render.yaml` (Blueprint) – damit kannst du den
Service auch per *New + → Blueprint* automatisch anlegen.

> 💤 **Hinweis Free-Plan:** Ein *Web Service* im Free-Plan schläft nach ~15 Min
> ohne Traffic ein. Der Bot öffnet über `keepalive.py` automatisch `$PORT`;
> richte zusätzlich einen Uptime-Pinger (z. B. UptimeRobot) auf die Render-URL
> ein. Dauerhaft stabil ist der *Background Worker*.

## 4. Lokal starten

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # Werte eintragen
python bot.py
```

---

## 🤖 Befehle

| Command | Wer? | Was? |
|---------|------|------|
| `/setup` | Administrator · Server-Owner · Bot-Owner | Kompletter Einrichtungs-Assistent |
| `/verify-panel [channel]` | Administrator · Owner | Verify-Embed (neu) senden |
| `/verify-config` | Administrator · Owner | Aktuelle Einstellungen anzeigen |
| `/verify-reset` | Administrator · Owner | Alles zurücksetzen |
| `/verify <member>` | Team-Rollen · Admins | Nutzer manuell verifizieren |
| `/sync` | Bot-Owner | Slash-Commands neu syncen |

## 🧭 Der `/setup`-Assistent

Der Bot fragt Schritt für Schritt ab – **jede Antwort wird sofort gespeichert**,
du kannst `/setup` jederzeit erneut ausführen und einzelne Punkte ändern:

1. **Verify-Kanal** – wohin das Embed mit dem Button gepostet wird
2. **Anfragen-Kanal** – wohin die Verifizierungs-Anfragen gehen (Team-Kanal)
3. **Team-Rollen** – wer Anfragen annehmen/ablehnen darf (wird gepingt)
4. **Rollen +** – welche Rollen ein verifizierter Nutzer **bekommt**
5. **Rollen −** – welche Rollen ihm **abgezogen** werden
6. **Auto-Rollen** – was jeder neue Nutzer beim Serverbeitritt automatisch bekommt
7. **Embed & Button** – Titel, Text, Farbe, Footer, Bild, Button-Text/Emoji/Farbe + Live-Vorschau
8. **Bewerbung (optional)** – Formular-Fragen und Checkboxen anlegen/bearbeiten/löschen,
   dazu die Schalter *Auto-Annahme* und *DM an Nutzer*
9. **Übersicht** → Button **🚀 Embed senden**

## 🔄 Ablauf für Nutzer

```
[Verifizieren]  ──►  Formular (bis zu 5 Fragen, Popup)
                     └►  Checkboxen (anklickbar, Pflicht/optional)
                          └►  Anfrage im Team-Kanal  ──►  [✅ Annehmen] / [❌ Ablehnen (+Grund)]
                                                            └►  Rollen +/−, DM an den Nutzer
```

* Sind **keine** Fragen/Checkboxen angelegt, geht die Anfrage direkt raus.
* Ist **Auto-Annahme** aktiv, wird sofort verifiziert (ohne Team-Prüfung).

## ✅ Warum der Button immer funktioniert

* Die Panel-View hat `timeout=None` und eine **feste `custom_id`** und wird in
  `setup_hook()` per `bot.add_view(...)` registriert → sie überlebt jeden Neustart.
* Die Annehmen/Ablehnen-Buttons nutzen `discord.ui.DynamicItem` mit der
  Anfrage-ID in der `custom_id` → auch alte Anfragen bleiben nach einem Deploy klickbar.
* Alle Konfigurationen liegen in einem **Cache**, der beim Start vorgeladen wird –
  der Klick wird also sofort beantwortet (kein „Interaktion fehlgeschlagen“).
* Kann etwas doch nicht rechtzeitig geladen werden, antwortet der Bot zuerst
  ephemeral und bietet einen **„Formular öffnen“**-Button an.

## 📁 Projektstruktur

```
bot.py                  Einstiegspunkt, Cache, persistente Views, Fehler-Handling
config.py               Env-Variablen, Defaults, Limits
database.py             Supabase/PostgreSQL (asyncpg) + Schema & Migrationen
utils.py                Rechte-Checks, Embed-Bau, Rollenvergabe, sichere Antworten
keepalive.py            Health-Server für Render Web Services ($PORT)
cogs/setup_cog.py       Slash-Commands
cogs/events.py          Auto-Rollen beim Join, Aufräumen
views/setup_wizard.py   /setup-Assistent, Embed-/Formular-/Checkbox-Editor
views/verify.py         Verify-Button, Formular, Checkboxen, Team-Prüfung
```

## 🛠️ Häufige Probleme

| Problem | Lösung |
|---------|--------|
| Slash-Commands fehlen | Bis zu 1 h global – `DEV_GUILD_ID` setzen oder `/sync` |
| „Kann Rolle nicht vergeben“ | Bot-Rolle nach **oben** ziehen + `Rollen verwalten` erlauben |
| Bot startet nicht (`SUPABASE_DB_URL fehlt`) | Env-Variable in Render prüfen |
| `SSL`/Verbindungsfehler zu Supabase | Pooler-URL (Port `6543`) verwenden |
| Keine Auto-Rollen | **SERVER MEMBERS INTENT** im Developer Portal aktivieren |
