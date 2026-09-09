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
    mal Bezirk, mal Ortsteil. Wer genauer werden will, nennt zusaetzlich PLZ.
    Ist gar nichts gesetzt, passt jede Lage.
    """
    bezirke = [_norm(b) for b in kriterien.get("bezirke") or []]
    plz_liste = [str(p).strip() for p in kriterien.get("plz") or []]
    plz_unklar = [str(p).strip() for p in kriterien.get("plz_unklar") or []]
    if not bezirke and not plz_liste and not plz_unklar:
        return "ja"

    gebiet = _norm(item.get("area"))
    plz = (item.get("zipCode") or "").strip()

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
