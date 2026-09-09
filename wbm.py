#!/usr/bin/env python3
"""Zugriff auf die Wohnungsangebote der WBM (www.wbm.de).

Anders als inberlinwohnen.de ist die WBM-Seite altmodisch: sie liefert alle
Angebote als fertiges HTML in einer einzigen Antwort. Kein JSON, keine
Nachlade-Komponenten, keine Paginierung - der Bestand ist mit gut einem Dutzend
Wohnungen klein genug, dass alles auf eine Seite passt.

Drei Dinge, die man wissen muss:

* Das Suchformular ist ein POST-Formular mit Sicherheits-Token
  (`__trustedProperties`, `__referrer`). Diese Token nachzubauen waere bruechig
  und wuerde bei jeder Aenderung des Formulars stillschweigend kaputtgehen.
  Deshalb wird hier *ungefiltert* geladen und in Python gefiltert - bei dieser
  Bestandsgroesse kostet das nichts und ist unabhaengig von der Seitenmechanik.
* `data-id` ist die Objektnummer aus dem OpenImmo-Export (z. B. `1-5327/1/1`)
  und bleibt an der Wohnung haengen. `data-uid` ist die interne Datenbank-ID
  und kann sich bei einem Neuimport aendern. Gemerkt wird darum `data-id`.
* Die Uebersicht zeigt nur die Warmmiete. Kaltmiete, WBS-Pflicht und
  Bezugstermin stehen erst auf der Detailseite - die wird nur fuer neue
  Wohnungen nachgeladen.
"""

import gzip
import html
import re
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://www.wbm.de/wohnungen-berlin/angebote/"
ORIGIN = "https://www.wbm.de"

# Ohne Browser-User-Agent antwortet die Seite mit 403.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip",
}

# Am Anfang jedes Angebots getrennt statt jeden Block einzeln gematcht: Wo ein
# Angebot endet, laesst sich mit regulaeren Ausdruecken nicht zuverlaessig
# sagen (verschachtelte <div>), der Anfang dagegen schon.
ITEM_SPLIT_RE = re.compile(
    r'<div class="row openimmo-search-list-item"\s+data-id="([^"]*)"\s+'
    r'data-uid="([^"]*)"'
)
TAG_RE = re.compile(r"<[^>]+>")


class WbmError(RuntimeError):
    pass


def _plain(value):
    """Markup entfernen, Entities aufloesen, Leerraum vereinheitlichen."""
    if value is None:
        return ""
    return " ".join(html.unescape(TAG_RE.sub(" ", str(value))).split()).strip(" ,;")


def zahl(value):
    """'2.571,82 €' -> 2571.82; None, wenn sich keine Zahl lesen laesst.

    Der Punkt ist im Deutschen Tausendertrenner, das Komma der Dezimalpunkt -
    aber die WBM schreibt die Groesse auf der Detailseite englisch als
    '120.46 m2'. Deshalb wird der Punkt nur dann als Trenner verworfen, wenn
    auch ein Komma vorkommt.
    """
    if value is None:
        return None
    text = re.sub(r"[^0-9.,]", "", str(value))
    if not text:
        return None
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def fetch(url, attempts=4, timeout=45):
    last_error = None
    for attempt in range(attempts):
        if attempt:
            time.sleep(2 ** attempt)
        try:
            request = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return raw.decode("utf-8", "replace")
        except (urllib.error.URLError, OSError, gzip.BadGzipFile) as exc:
            last_error = exc
    raise WbmError("Seite %s nicht erreichbar: %s" % (url, last_error))


def _absolut(href):
    if not href:
        return ""
    return urllib.parse.urljoin(ORIGIN, html.unescape(href))


def _search(pattern, text, gruppe=1):
    match = re.search(pattern, text, re.S)
    return _plain(match.group(gruppe)) if match else ""


def parse_list(page_html):
    """Die Angebotsuebersicht in eine Liste von Wohnungen zerlegen."""
    stuecke = ITEM_SPLIT_RE.split(page_html)
    # split() liefert [vorspann, id, uid, block, id, uid, block, ...]
    bloecke = [tuple(stuecke[i:i + 3]) for i in range(1, len(stuecke) - 2, 3)]

    items = []
    for objektnummer, uid, block in bloecke:
        adresse = _search(r'<div class="address">(.*?)</div>', block)
        plz = ""
        treffer = re.search(r"\b(1[0-4]\d{3})\b", adresse)
        if treffer:
            plz = treffer.group(1)
        strasse = adresse.split(",")[0].strip() if "," in adresse else adresse

        items.append({
            "id": html.unescape(objektnummer),
            "uid": uid,
            "title": _search(r'<h2 class="imageTitle">(.*?)</h2>', block),
            "address": adresse,
            "street": strasse,
            "zipCode": plz,
            "area": _search(r'<div class="area">(.*?)</div>', block),
            "rentGross": _search(
                r'main-property-value main-property-rent">(.*?)</div>', block),
            "size": _search(
                r'main-property-value main-property-size">(.*?)</div>', block),
            "rooms": _search(
                r'main-property-value main-property-rooms">(.*?)</div>', block),
            "features": [
                _plain(x) for x in re.findall(
                    r"<li>(.*?)</li>",
                    _search_raw(r'<ul class="check-property-list">(.*?)</ul>', block),
                ) if _plain(x)
            ],
            "deeplink": _absolut(
                _search_raw(r'<a class="immo-button-cta"[^>]*href="([^"]*)"', block)),
            # Wird bei Bedarf aus der Detailseite ergaenzt.
            "rentNet": "",
            "extraCosts": "",
            "wbs": None,
            "availableFrom": "",
            "floor": "",
        })
    return items


def _search_raw(pattern, text, gruppe=1):
    """Wie _search, aber ohne das Markup zu entfernen."""
    match = re.search(pattern, text, re.S)
    return match.group(gruppe) if match else ""


def parse_detail(detail_html):
    """Kaltmiete, Nebenkosten, WBS-Pflicht und Bezugstermin herausziehen.

    Bewusst nachsichtig: Die Detailseite ist stark verschachtelt, und ein
    fehlendes Feld darf die Meldung nicht verhindern. Was nicht gefunden wird,
    bleibt leer - die Wohnung wird trotzdem gemeldet.
    """
    text = _plain(re.sub(r"(?s)<(script|style).*?</\1>", " ", detail_html))
    daten = {}

    def nach(label, muster=r"([\d.,]+)\s*(?:EUR|€)"):
        treffer = re.search(re.escape(label) + r"\s*" + muster, text)
        return treffer.group(1) if treffer else ""

    daten["rentNet"] = nach("Nettokaltmiete")
    daten["extraCosts"] = nach("Nebenkosten")

    treffer = re.search(r"WBS erforderlich:\s*(Ja|Nein)", text, re.I)
    if treffer:
        daten["wbs"] = treffer.group(1).lower() == "ja"

    # Zimmerzahl, Groesse und Warmmiete stehen bereits in der Uebersicht - und
    # dort mit Einheit. Aus der Detailseite kommt nur, was dort fehlt.
    for schluessel, muster in (
        ("availableFrom", r"Bezugsfertig ab:\s*(.{0,30}?)(?=\s*WBS erforderlich|\s*Merkmale|$)"),
        ("floor", r"Etage:\s*(-?\d+)"),
    ):
        treffer = re.search(muster, text)
        if treffer:
            daten[schluessel] = treffer.group(1).strip()

    return {k: v for k, v in daten.items() if v not in ("", None) or k == "wbs"}


def enrich(item, timeout=45):
    """Detailseite nachladen und die Wohnung ergaenzen. Fehler sind egal."""
    if not item.get("deeplink"):
        return item
    try:
        detail = fetch(item["deeplink"], attempts=2, timeout=timeout)
    except WbmError:
        return item
    for schluessel, wert in parse_detail(detail).items():
        if wert not in ("", None):
            item[schluessel] = wert
    return item


def collect():
    """Alle aktuell inserierten Wohnungen laden.

    Rueckgabe: (liste_der_ids, wohnungen_nach_id).
    """
    page = fetch(BASE_URL)
    items = parse_list(page)
    if not items:
        # Zwischen "gerade keine Wohnung frei" und "Aufbau geaendert"
        # unterscheiden: die Seite nennt die Trefferzahl in der Ueberschrift.
        treffer = re.search(r"(\d+)\s+Mietwohnungen", page)
        if treffer and treffer.group(1) == "0":
            return [], {}
        raise WbmError(
            "Keine Angebote im Seitenquelltext gefunden - hat sich der "
            "Seitenaufbau geaendert?"
        )
    by_id = {}
    for item in items:
        by_id.setdefault(item["id"], item)
    return [i["id"] for i in items], by_id
