#!/usr/bin/env python3
"""Beobachtet die Wohnungsangebote der WBM und mailt neue Inserate.

Aufruf:
    python3 watch.py            # ein Durchlauf
    python3 watch.py --dry-run  # nichts versenden, nichts speichern
    python3 watch.py --test-mail # Testmail verschicken und beenden
    python3 watch.py --dauer 1680 # 28 Minuten lang im Takt weiterpruefen
"""

import argparse
import datetime
import json
import os
import smtplib
import ssl
import sys
import time
import urllib.parse
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

import kriterien
import wbm

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
STATE_PATH = os.path.join(HERE, "state", "seen.json")

# IDs, die so lange nicht mehr in den Treffern auftauchten, werden vergessen.
# Damit waechst die Zustandsdatei nicht unbegrenzt, und eine Wohnung, die nach
# Monaten neu inseriert wird, gilt zu Recht wieder als neu.
FORGET_AFTER_DAYS = 90

# Bei Stoerungen (Seite nicht erreichbar, Aufbau geaendert) hoechstens so oft
# eine Warnmail - sonst kaeme bei einem laengeren Ausfall alle 5 Minuten eine.
ERROR_MAIL_EVERY_HOURS = 12

# Takt im Schleifenbetrieb (--dauer). Die WBM stellt ihre Wohnungen zu
# Buerozeiten ein, nicht nachts und kaum am Wochenende. Deshalb tagsueber
# jede Minute schauen und sonst deutlich seltener - das haelt die Last auf der
# fremden Seite in dem Rahmen, den ein aufmerksamer Mensch auch erzeugt.
# Ein Aufruf ist eine einzige HTML-Seite; Detailseiten werden nur fuer neue
# Wohnungen geholt.
TAKT_AKTIV_SEKUNDEN = 60
TAKT_RUHE_SEKUNDEN = 300
# In UTC, denn der GitHub-Runner laeuft in UTC: 05-20 Uhr UTC deckt die
# Berliner Kernzeit im Sommer wie im Winter ab.
AKTIV_STUNDEN_UTC = range(5, 21)


def taktweite(stamp):
    """Sekunden bis zur naechsten Pruefung."""
    werktag = stamp.weekday() < 5
    if werktag and stamp.hour in AKTIV_STUNDEN_UTC:
        return TAKT_AKTIV_SEKUNDEN
    return TAKT_RUHE_SEKUNDEN


def now():
    return datetime.datetime.now(datetime.timezone.utc)


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def save_json(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1, sort_keys=True)
        handle.write("\n")
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# E-Mail
# --------------------------------------------------------------------------

def load_env_file():
    """Lokal hinterlegte Zugangsdaten aus .env uebernehmen.

    In GitHub Actions gibt es diese Datei nicht - dort kommen die Werte aus den
    Secrets. Bereits gesetzte Umgebungsvariablen haben Vorrang.
    """
    path = os.path.join(HERE, ".env")
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def env(name, default=None):
    """Umgebungsvariable lesen und Leerstrings wie 'nicht gesetzt' behandeln.

    Noetig wegen GitHub Actions: Dort wird `${{ secrets.X }}` auch dann in die
    Umgebung geschrieben, wenn das Secret gar nicht existiert - eben als leerer
    Text. Ein schlichtes os.environ.get(name, "587") liefert dann "" statt der
    Vorgabe, und der Lauf scheitert an einer Stelle, die mit der eigentlichen
    Ursache nichts zu tun hat.
    """
    wert = os.environ.get(name)
    if wert is None or not wert.strip():
        return default
    return wert.strip()


def smtp_settings():
    load_env_file()
    missing = [
        name
        for name in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "MAIL_TO")
        if not env(name)
    ]
    if missing:
        raise SystemExit(
            "Fehlende Konfiguration: %s\n"
            "Lokal per Umgebungsvariablen setzen, in GitHub Actions als Secrets."
            % ", ".join(missing)
        )
    roher_port = env("SMTP_PORT", "587")
    try:
        port = int(roher_port)
    except ValueError:
        raise SystemExit(
            "SMTP_PORT ist keine Zahl: %r\n"
            "Ueblich sind 587 (STARTTLS) oder 465 (durchgehend verschluesselt)."
            % roher_port
        )

    # Port 587 spricht Klartext und schaltet per STARTTLS auf TLS um, Port 465
    # ist von Anfang an verschluesselt. SMTP_SSL erlaubt es, das bei
    # abweichenden Ports selbst festzulegen.
    ssl_wahl = env("SMTP_SSL")
    if ssl_wahl is None:
        implicit_tls = port == 465
    else:
        implicit_tls = ssl_wahl.lower() in ("1", "true", "yes", "ja")

    return {
        "host": env("SMTP_HOST"),
        "port": port,
        "implicit_tls": implicit_tls,
        "user": env("SMTP_USER"),
        "password": os.environ["SMTP_PASS"],
        "sender": env("MAIL_FROM") or env("SMTP_USER"),
        "recipients": [
            a.strip() for a in env("MAIL_TO", "").split(",") if a.strip()
        ],
    }


def send_or_explain(subject, text_body, html_body=None):
    """Verschicken und im Fehlerfall erklaeren, aber den Fehler durchreichen.

    Das Durchreichen ist wichtig: Nur so bricht der Lauf ab, bevor die
    Wohnungen als gemeldet vermerkt werden - sonst waeren sie verloren.
    """
    try:
        return send_mail(subject, text_body, html_body)
    except Exception as fehler:  # noqa: BLE001 - wird erklaert und neu geworfen
        print(diagnose(fehler), file=sys.stderr)
        raise


def diagnose(fehler):
    """Aus einer SMTP-Ausnahme eine verstaendliche Erklaerung machen.

    Laeuft vor allem in GitHub Actions: Dort liest niemand gern einen Traceback,
    und der haeufigste Fall - der Mailserver laesst den Versand aus fremden
    Netzen nicht zu - sieht einem falschen Passwort zum Verwechseln aehnlich.
    """
    host = os.environ.get("SMTP_HOST", "(unbekannt)")
    text = str(fehler)
    kopf = "MAILVERSAND FEHLGESCHLAGEN (%s)" % host
    strich = "=" * len(kopf)

    if isinstance(fehler, smtplib.SMTPAuthenticationError):
        grund = (
            "Der Server hat die Anmeldung abgelehnt.\n\n"
            "Da derselbe Zugang lokal funktioniert hat, ist die wahrscheinlichste\n"
            "Ursache: Dieser Mailserver erlaubt den Versand nur aus dem eigenen\n"
            "Netz - und GitHub laeuft in einem Rechenzentrum.\n\n"
            "Zweite Moeglichkeit: In den Secrets steckt ein Tippfehler.\n"
            "Pruefe SMTP_USER und SMTP_PASS, danach hilft nur ein Wechsel des\n"
            "Absenders (z. B. web.de oder GMX)."
        )
    elif isinstance(fehler, smtplib.SMTPSenderRefused):
        grund = (
            "Der Server akzeptiert die Absenderadresse nicht.\n"
            "MAIL_FROM muss meist zum Anmeldenamen passen."
        )
    elif isinstance(fehler, smtplib.SMTPRecipientsRefused):
        grund = "Der Server hat die Empfaengeradresse abgelehnt (MAIL_TO pruefen)."
    elif isinstance(fehler, (smtplib.SMTPConnectError, TimeoutError, OSError)):
        grund = (
            "Es kam keine Verbindung zustande.\n\n"
            "Moeglich: Der Server blockt Verbindungen aus Rechenzentren, oder\n"
            "SMTP_HOST bzw. SMTP_PORT stimmen nicht."
        )
    else:
        grund = "Unerwarteter Fehler."

    return "\n%s\n%s\n\n%s\n\nTechnische Meldung: %s\n" % (
        kopf, strich, grund, text)


def send_mail(subject, text_body, html_body=None):
    cfg = smtp_settings()
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = cfg["sender"]
    message["To"] = ", ".join(cfg["recipients"])
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain="wbm-watcher.local")
    message.set_content(text_body)
    if html_body:
        message.add_alternative(html_body, subtype="html")

    context = ssl.create_default_context()
    if cfg["implicit_tls"]:
        server = smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=60, context=context)
    else:
        server = smtplib.SMTP(cfg["host"], cfg["port"], timeout=60)
    with server:
        server.ehlo()
        if not cfg["implicit_tls"]:
            server.starttls(context=context)
            server.ehlo()
        server.login(cfg["user"], cfg["password"])
        server.send_message(message)
    return cfg["recipients"]


# --------------------------------------------------------------------------
# Darstellung
# --------------------------------------------------------------------------

def wert(item, schluessel):
    return (item.get(schluessel) or "").strip()


def headline(item):
    parts = []
    if wert(item, "rooms"):
        parts.append("%s Zi." % wert(item, "rooms"))
    if wert(item, "size"):
        parts.append(wert(item, "size").replace("m²", "m2"))
    if wert(item, "rentNet"):
        parts.append("%s EUR kalt" % wert(item, "rentNet"))
    elif wert(item, "rentGross"):
        parts.append("%s warm" % wert(item, "rentGross").replace("€", "EUR"))
    where = wert(item, "area") or "Berlin"
    return "%s - %s" % (" / ".join(parts) if parts else "Wohnung", where)


def detailzeilen(item):
    """Beschriftete Werte fuer Mailtext und HTML-Tabelle - eine Quelle fuer beide."""
    zeilen = [
        ("Warmmiete", wert(item, "rentGross").replace("€", "EUR")),
        ("Kaltmiete", "%s EUR" % wert(item, "rentNet") if wert(item, "rentNet") else ""),
        ("Nebenkosten",
         "%s EUR" % wert(item, "extraCosts") if wert(item, "extraCosts") else ""),
        ("Zimmer", wert(item, "rooms")),
        ("Groesse", wert(item, "size").replace("m²", "m2")),
        ("Etage", wert(item, "floor")),
        ("Bezugsfertig ab", wert(item, "availableFrom")),
        ("Objektnummer", wert(item, "id")),
    ]
    if item.get("wbs") is not None:
        zeilen.append(("WBS erforderlich", "ja" if item["wbs"] else "nein"))
    if item.get("features"):
        zeilen.append(("Merkmale", ", ".join(item["features"])))
    return [(label, value) for label, value in zeilen if value]


def item_text(item):
    lines = [headline(item)]
    if wert(item, "address"):
        lines.append("  " + wert(item, "address"))
    for label, value in detailzeilen(item):
        lines.append("  %s: %s" % (label, value))
    if item.get("ortsteilUnklar"):
        lines.append("  Achtung: Diese PLZ liegt auf einer Ortsteilgrenze - "
                     "bitte pruefen, ob die Lage passt.")
    if wert(item, "title"):
        lines.append("  Hinweis: %s" % wert(item, "title"))
    if wert(item, "deeplink"):
        lines.append("  Zum Angebot: %s" % wert(item, "deeplink"))
    return "\n".join(lines)


def esc(value):
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def item_html(item):
    rows = "".join(
        '<tr><td style="padding:2px 12px 2px 0;color:#555;white-space:nowrap;">%s</td>'
        '<td style="padding:2px 0;">%s</td></tr>' % (esc(label), esc(value))
        for label, value in detailzeilen(item)
    )
    badge = ""
    if item.get("ortsteilUnklar"):
        badge += (
            '<span style="background:#e0e7ff;color:#3730a3;border-radius:4px;'
            'padding:1px 6px;font-size:12px;margin-left:8px;">Ortsteil pruefen'
            '</span>'
        )
    if item.get("wbs"):
        badge += (
            '<span style="background:#fde68a;color:#78350f;border-radius:4px;'
            'padding:1px 6px;font-size:12px;margin-left:8px;">WBS</span>'
        )
    button = ""
    if wert(item, "deeplink"):
        button = (
            '<p style="margin:12px 0 0;"><a href="%s" '
            'style="background:#1d4ed8;color:#fff;text-decoration:none;'
            'padding:8px 14px;border-radius:6px;display:inline-block;">'
            'Zum Angebot bei der WBM</a></p>' % esc(wert(item, "deeplink"))
        )
    note = ""
    if wert(item, "title"):
        note = (
            '<p style="margin:8px 0 0;color:#444;font-style:italic;">%s</p>'
            % esc(wert(item, "title"))
        )
    return (
        '<div style="border:1px solid #e5e7eb;border-radius:8px;padding:16px;'
        'margin:0 0 16px;">'
        '<h2 style="margin:0 0 4px;font-size:17px;">%s%s</h2>'
        '<p style="margin:0 0 10px;color:#555;">%s</p>'
        '<table style="border-collapse:collapse;font-size:14px;">%s</table>'
        "%s%s</div>"
        % (esc(headline(item)), badge, esc(wert(item, "address")), rows, note, button)
    )


def build_mail(items, config, note=None, seeding=False):
    link = wbm.BASE_URL
    count = len(items)

    if seeding:
        subject = "WBM-Watcher aktiv (%d passende Wohnungen im Bestand)" % count
        intro = (
            "Der Waechter laeuft ab jetzt. Die aktuell angebotenen Wohnungen "
            "gelten als bekannt; ab sofort bekommst du nur noch neue Inserate "
            "gemeldet. Unten die passenden zur Kontrolle."
        )
        shown = items[:3]
    else:
        subject = "%d neue WBM-Wohnung%s: %s" % (
            count,
            "en" if count != 1 else "",
            ", ".join(sorted({wert(i, "area") for i in items if wert(i, "area")}))
            or "Berlin",
        )
        intro = "Neu im Wohnungsangebot der WBM:"
        shown = items

    blocks = [intro]
    if note:
        blocks.append("ACHTUNG: " + note)
    blocks += [item_text(i) for i in shown]
    blocks.append("Alle Angebote der WBM: " + link)

    warning = ""
    if note:
        warning = (
            '<p style="background:#fef3c7;border-left:4px solid #f59e0b;'
            'padding:10px 14px;margin:0 0 16px;">%s</p>' % esc(note)
        )
    html_body = (
        '<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;'
        'max-width:640px;margin:0 auto;padding:8px;color:#111;">'
        '<p style="margin:0 0 16px;">%s</p>%s%s'
        '<p style="margin:20px 0 0;font-size:14px;">'
        '<a href="%s">Alle Angebote der WBM ansehen</a></p>'
        '<p style="margin:24px 0 0;font-size:12px;color:#888;">'
        "Automatische Nachricht deines WBM-Waechters.</p></div>"
        % (esc(intro), warning, "".join(item_html(i) for i in shown), esc(link))
    )
    return subject, "\n\n".join(blocks), html_body


# --------------------------------------------------------------------------
# Ablauf
# --------------------------------------------------------------------------

def prune(seen, stamp):
    cutoff = (stamp - datetime.timedelta(days=FORGET_AFTER_DAYS)).date()
    kept = {}
    for key, value in seen.items():
        try:
            when = datetime.date.fromisoformat(str(value)[:10])
        except (TypeError, ValueError):
            continue
        if when >= cutoff:
            kept[key] = value
    return kept


def report_error(state, message, dry_run):
    """Warnmail schicken, aber hoechstens alle ERROR_MAIL_EVERY_HOURS Stunden."""
    stamp = now()
    last = state.get("last_error_mail")
    if last:
        try:
            age = stamp - datetime.datetime.fromisoformat(last)
            if age < datetime.timedelta(hours=ERROR_MAIL_EVERY_HOURS):
                return False
        except (TypeError, ValueError):
            pass
    if dry_run:
        print("[dry-run] Warnmail waere verschickt worden: %s" % message)
        return False
    send_mail(
        "WBM-Watcher: Problem beim Abrufen",
        "Der Waechter konnte die WBM-Angebotsseite nicht auswerten.\n\n"
        "%s\n\nEr versucht es beim naechsten Lauf erneut. Kommt diese Meldung "
        "wiederholt, hat sich vermutlich der Aufbau der Seite geaendert."
        % message,
    )
    state["last_error_mail"] = stamp.isoformat()
    return True


def durchlauf(args):
    load_env_file()
    config = load_json(CONFIG_PATH, {})
    # Die Kriterien gehoeren niemandem ausser dir: in einem oeffentlichen
    # Repository stehen sie im Secret WBM_KRITERIEN (lokal in .env),
    # config.json dient nur als Rueckfallebene.
    filter_kriterien = kriterien.laden(config)
    if not any(filter_kriterien[name] for name in kriterien.VORGABE
               if name != "wbs"):
        print("Warnung: keine Suchkriterien gesetzt (WBM_KRITERIEN) - es wird "
              "jede neue Wohnung gemeldet.", file=sys.stderr)

    state = load_json(STATE_PATH, {})
    seen = state.get("seen") or {}
    seeding = not seen

    try:
        item_ids, by_id = wbm.collect()
    except wbm.WbmError as exc:
        print("Fehler: %s" % exc, file=sys.stderr)
        if report_error(state, str(exc), args.dry_run) and not args.dry_run:
            save_json(STATE_PATH, state)
        return 1

    state.pop("last_error_mail", None)
    stamp = now()
    new_ids = [i for i in item_ids if str(i) not in seen]
    print("%d Angebote gesamt, %d davon neu%s"
          % (len(item_ids), len(new_ids), " (Erstlauf)" if seeding else ""))

    # Kaltmiete, WBS-Pflicht und Bezugstermin stehen nur auf der Detailseite.
    # Die wird ausschliesslich fuer neue Wohnungen geholt - im Regelfall also
    # gar nicht, und bei Bewegung fuer eine Handvoll.
    #
    # Grobfilter zuerst: Was schon an Lage, Zimmerzahl oder Warmmiete
    # scheitert, braucht keine zweite Anfrage. Erst danach wird nachgeladen und
    # mit den vollstaendigen Angaben endgueltig entschieden.
    kandidaten = [by_id[i] for i in new_ids
                  if i in by_id and kriterien.passt(by_id[i], filter_kriterien)]
    for item in kandidaten:
        wbm.enrich(item)

    new_items = []
    for item in kandidaten:
        if not kriterien.passt(item, filter_kriterien):
            continue
        item["ortsteilUnklar"] = (
            kriterien.lage_status(item, filter_kriterien) == "unklar"
        )
        new_items.append(item)

    aussortiert = len(new_ids) - len(new_items)
    if aussortiert:
        print("%d von %d neuen Wohnungen passen nicht zu deinen Kriterien."
              % (aussortiert, len(new_ids)))

    if seeding:
        # Beim ersten Lauf nicht den kompletten Bestand mailen.
        if new_items and not args.dry_run:
            subject, text, html_body = build_mail(new_items, config, seeding=True)
            to = send_or_explain(subject, text, html_body)
            print("Startmail verschickt an: %s" % ", ".join(to))
        elif args.dry_run:
            print("[dry-run] Startmail: %d Angebote im Bestand, %d davon passend"
                  % (len(item_ids), len(new_items)))
    elif new_items:
        subject, text, html_body = build_mail(new_items, config)
        if args.dry_run:
            print("[dry-run] Mail: %s\n\n%s" % (subject, text))
        else:
            to = send_or_explain(subject, text, html_body)
            print("Mail verschickt an: %s" % ", ".join(to))

    if args.dry_run:
        return 0

    # Nur auf den Tag genau vermerken: Der Workflow schreibt die Datei nach
    # jedem Lauf ins Repository zurueck, und ein sekundengenauer Zeitstempel
    # wuerde sie bei jedem Lauf aendern - hunderte Commits taeglich, nur weil
    # die Uhr weitergelaufen ist. Fuer das Vergessen nach 90 Tagen genuegt das
    # Datum, und die Datei aendert sich jetzt nur noch, wenn sich der
    # Wohnungsbestand tatsaechlich bewegt hat.
    heute = stamp.date().isoformat()
    marked = dict(seen)
    for item_id in item_ids:
        marked[str(item_id)] = heute
    state["seen"] = prune(marked, stamp)
    if state != load_json(STATE_PATH, None):
        save_json(STATE_PATH, state)
        print("Zustand aktualisiert.")
    else:
        print("Zustand unveraendert.")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="nichts versenden und nichts speichern")
    parser.add_argument("--test-mail", action="store_true",
                        help="nur eine Testmail verschicken")
    parser.add_argument("--dauer", type=int, default=0, metavar="SEKUNDEN",
                        help="so lange im Takt weiterpruefen statt einmal "
                             "(z. B. 1680 fuer 28 Minuten)")
    args = parser.parse_args()

    if args.test_mail:
        try:
            to = send_mail(
                "WBM-Watcher: Testmail",
                "Wenn du das liest, funktioniert der Mailversand.",
                '<p style="font-family:sans-serif;">Wenn du das liest, funktioniert '
                "der Mailversand.</p>",
            )
        except Exception as fehler:  # noqa: BLE001 - Ursache wird uebersetzt
            print(diagnose(fehler), file=sys.stderr)
            return 1
        print("Testmail verschickt an: %s" % ", ".join(to))
        return 0

    if args.dauer <= 0:
        return durchlauf(args)

    # Schleifenbetrieb: GitHub haelt den 5-Minuten-Takt seiner geplanten Laeufe
    # nicht ein - unter Last vergehen 10 bis 30 Minuten. Ein Job, der laenger
    # laeuft und selbst im Takt nachschaut, ist deutlich puenktlicher. Der
    # Zustand wird dabei nur in die Datei geschrieben; ins Repository
    # zurueckgeschrieben wird er einmal am Jobende durch den Workflow.
    schluss = time.monotonic() + args.dauer
    runde = 0
    while True:
        runde += 1
        print("--- Durchlauf %d (%s UTC)"
              % (runde, now().strftime("%H:%M:%S")), flush=True)
        try:
            durchlauf(args)
        except Exception as fehler:  # noqa: BLE001 - ein Aussetzer darf den
            # laufenden Job nicht beenden, sonst schweigt der Waechter bis zum
            # naechsten geplanten Start.
            print("Durchlauf fehlgeschlagen: %s" % fehler, file=sys.stderr,
                  flush=True)

        pause = taktweite(now())
        if time.monotonic() + pause >= schluss:
            print("Zeitfenster ausgeschoepft nach %d Durchlaeufen." % runde,
                  flush=True)
            return 0
        time.sleep(pause)


if __name__ == "__main__":
    sys.exit(main())
