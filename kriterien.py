#!/usr/bin/env python3
"""Auswahl der Wohnungen nach eigenen Kriterien.

Die WBM-Suche ist ein POST-Formular mit Sicherheits-Token; sie serverseitig
fernzusteuern waere bruechig. Weil der Bestand klein ist (ein gutes Dutzend
Wohnungen), wird stattdessen alles geladen und hier gefiltert. Das hat einen
angenehmen Nebeneffekt: Es laesst sich nach Dingen filtern, die das Formular
gar nicht anbietet - Ortsteil, Kaltmiete, WBS-Pflicht.

Die Kriterien stehen in config.json und lassen sich mit der Umgebungsvariablen
WBM_KRITERIEN (JSON) ueberschreiben, wenn sie in einem oeffentlichen Repository
nichts zu suchen haben.
"""

import json
import os

import wbm

VORGABE = {
    "bezirke_aus": [],
    "gebiete_mit_plz": {},
    "bezirke": [],
    "plz": [],
    "plz_unklar": [],
    "min_zimmer": None,
    "max_zimmer": None,
    "min_flaeche": None,
    "max_flaeche": None,
    "max_kaltmiete": None,
    "max_warmmiete": None,
    "wbs": "egal",
}


def laden(config, umgebung=None):
    """Kriterien aus config.json, ueberschrieben von WBM_KRITERIEN."""
    kriterien = dict(VORGABE)
    kriterien.update(
        {k: v for k, v in (config.get("kriterien") or {}).items() if k in VORGABE}
    )
    roh = (umgebung if umgebung is not None else os.environ.get("WBM_KRITERIEN")) or ""
    if roh.strip():
        try:
            kriterien.update(
                {k: v for k, v in json.loads(roh).items() if k in VORGABE}
            )
        except ValueError as fehler:
            raise SystemExit(
                "WBM_KRITERIEN ist kein gueltiges JSON: %s" % fehler
            )
    return kriterien


def _norm(text):
    return (text or "").strip().casefold()


def lage_status(item, kriterien):
    """'ja', 'unklar' oder 'nein' - liegt die Wohnung im Wunschgebiet?

    Die WBM nennt in der Uebersicht ein Gebiet ("Mitte", "Friedrichshain") -
    mal Bezirk, mal Ortsteil. Ist gar nichts gesetzt, passt jede Lage.

    Vier Stufen, in dieser Reihenfolge:

    1. `bezirke_aus` schliesst ganze Gebiete aus. So laesst sich "ueberall,
       nur nicht dort" sagen, ohne alle erwuenschten Gebiete aufzuzaehlen -
       eine Liste, die unvollstaendig waere, sobald die WBM in einer bislang
       unbekannten Gegend baut.
    2. `gebiete_mit_plz` grenzt einzelne Gebiete auf Ortsteile ein: dort zaehlt
       nur noch die Postleitzahl. Fuer Bezirke, von denen nur ein Teil in Frage
       kommt (Lichtenberg ja, Friedrichsfelde nein).
    3. `bezirke` und `plz` sind die einfache Positivliste.
    4. Ist keine davon gesetzt, passt jede Lage.
    """
    gebiet_roh = (item.get("area") or "").strip()
    plz_roh = (item.get("zipCode") or "").strip()

    if _norm(gebiet_roh) in [_norm(b) for b in kriterien.get("bezirke_aus") or []]:
        return "nein"

    fein = {
        _norm(name): regel
        for name, regel in (kriterien.get("gebiete_mit_plz") or {}).items()
    }
    regel = fein.get(_norm(gebiet_roh))
    if regel is not None:
        if plz_roh in [str(p).strip() for p in regel.get("ja") or []]:
            return "ja"
        if plz_roh in [str(p).strip() for p in regel.get("unklar") or []]:
            return "unklar"
        # Ohne PLZ laesst sich nichts entscheiden - lieber melden als verlieren.
        return "unklar" if not plz_roh else "nein"

    bezirke = [_norm(b) for b in kriterien.get("bezirke") or []]
    plz_liste = [str(p).strip() for p in kriterien.get("plz") or []]
    plz_unklar = [str(p).strip() for p in kriterien.get("plz_unklar") or []]
    if not bezirke and not plz_liste and not plz_unklar:
        return "ja"

    gebiet = _norm(gebiet_roh)
    plz = plz_roh

    if plz and plz in plz_unklar:
        return "unklar"
    if bezirke and gebiet in bezirke:
        return "ja"
    if plz and plz in plz_liste:
        return "ja"
    if not gebiet and not plz:
        # Nichts zu entscheiden - lieber melden als verlieren.
        return "unklar"
    return "nein"


def _zahl(item, schluessel):
    return wbm.zahl(item.get(schluessel))


def passt(item, kriterien):
    """True, wenn die Wohnung allen gesetzten Kriterien genuegt.

    Nicht lesbare Werte gelten als "passt": ein fehlender Preis darf keine
    Wohnung verschwinden lassen, die sonst in Frage kaeme.
    """
    grenzen = (
        ("min_zimmer", "rooms", lambda w, g: w >= g),
        ("max_zimmer", "rooms", lambda w, g: w <= g),
        ("min_flaeche", "size", lambda w, g: w >= g),
        ("max_flaeche", "size", lambda w, g: w <= g),
        ("max_kaltmiete", "rentNet", lambda w, g: w <= g),
        ("max_warmmiete", "rentGross", lambda w, g: w <= g),
    )
    for name, feld, pruefung in grenzen:
        grenze = kriterien.get(name)
        if grenze is None:
            continue
        wert = _zahl(item, feld)
        if wert is not None and not pruefung(wert, float(grenze)):
            return False

    wbs_wunsch = _norm(kriterien.get("wbs")) or "egal"
    if wbs_wunsch != "egal" and item.get("wbs") is not None:
        if wbs_wunsch in ("nein", "ohne", "false") and item["wbs"]:
            return False
        if wbs_wunsch in ("ja", "nur", "true") and not item["wbs"]:
            return False

    return lage_status(item, kriterien) != "nein"
