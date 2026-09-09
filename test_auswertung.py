#!/usr/bin/env python3
"""Prueft das Auswerten der WBM-Seite gegen gespeicherte Beispielseiten.

Der Watcher liest fremdes HTML - der einzige Bauteil, der ohne eigenes
Zutun kaputtgehen kann. Diese Pruefungen laufen gegen die Kopien in
`testdaten/` und schlagen deshalb genau dann fehl, wenn jemand den Parser
kaputtmacht; ob die WBM ihre Seite umgebaut hat, verraet stattdessen der
Aufruf mit --live.

Aufruf:
    python3 test_auswertung.py          # nur gespeicherte Seiten
    python3 test_auswertung.py --live   # zusaetzlich die echte Seite abrufen
"""

import os
import sys

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HIER)

import kriterien  # noqa: E402
import wbm  # noqa: E402

pruefungen = []


def pruefe(beschreibung, bedingung):
    pruefungen.append((beschreibung, bool(bedingung)))


def lies(name):
    with open(os.path.join(HIER, "testdaten", name), encoding="utf-8") as handle:
        return handle.read()


# --- Uebersichtsseite -----------------------------------------------------

items = wbm.parse_list(lies("angebote.html"))
pruefe("alle 13 Angebote der Beispielseite gefunden", len(items) == 13)
pruefe("jedes Angebot hat eine Objektnummer", all(i["id"] for i in items))
pruefe("Objektnummern sind eindeutig", len({i["id"] for i in items}) == len(items))
pruefe("jedes Angebot hat einen Link",
       all(i["deeplink"].startswith("https://www.wbm.de/") for i in items))

erstes = items[0]
pruefe("Objektnummer gelesen", erstes["id"] == "1-5327/1/1")
pruefe("Titel gelesen", "Terrasse in Mitte" in erstes["title"])
pruefe("Adresse gelesen", erstes["address"] == "Köpenicker Strasse 105, 10179 Berlin")
pruefe("PLZ aus der Adresse gezogen", erstes["zipCode"] == "10179")
pruefe("Gebiet gelesen", erstes["area"] == "Mitte")
pruefe("Warmmiete gelesen", erstes["rentGross"] == "2.571,82 €")
pruefe("Groesse gelesen", erstes["size"] == "120,46 m²")
pruefe("Zimmerzahl gelesen", erstes["rooms"] == "4")
pruefe("Merkmale gelesen", "Aufzug" in erstes["features"])
pruefe("Kaltmiete steht noch nicht in der Uebersicht", erstes["rentNet"] == "")

# --- Detailseite ----------------------------------------------------------

detail = wbm.parse_detail(lies("detail.html"))
pruefe("Nettokaltmiete gelesen", detail.get("rentNet") == "2.065,89")
pruefe("Nebenkosten gelesen", detail.get("extraCosts") == "505,93")
pruefe("WBS-Pflicht gelesen", detail.get("wbs") is False)
pruefe("Bezugstermin gelesen", detail.get("availableFrom") == "sofort")
pruefe("Etage gelesen", detail.get("floor") == "1")
pruefe("Uebersichtswerte werden nicht ueberschrieben",
       "rooms" not in detail and "size" not in detail and "rentGross" not in detail)

# --- Zahlen ---------------------------------------------------------------

pruefe("deutsche Schreibweise gelesen", wbm.zahl("2.571,82 €") == 2571.82)
pruefe("englische Schreibweise gelesen", wbm.zahl("120.46 m²") == 120.46)
pruefe("Text ohne Zahl ergibt None", wbm.zahl("auf Anfrage") is None)
pruefe("leerer Wert ergibt None", wbm.zahl(None) is None)

# --- Kriterien ------------------------------------------------------------

beispiel = dict(items[0], rentNet="2.065,89", wbs=False)
leer = kriterien.laden({})
pruefe("ohne Kriterien passt jede Wohnung", kriterien.passt(beispiel, leer))
pruefe("ohne Kriterien ist die Lage nie unpassend",
       kriterien.lage_status(beispiel, leer) == "ja")

streng = kriterien.laden({"kriterien": {"max_kaltmiete": 900}})
pruefe("zu teure Wohnung faellt raus", not kriterien.passt(beispiel, streng))

lage = kriterien.laden({"kriterien": {"bezirke": ["Friedrichshain"]}})
pruefe("fremdes Gebiet faellt raus", not kriterien.passt(beispiel, lage))
lage = kriterien.laden({"kriterien": {"bezirke": ["mitte"]}})
pruefe("Gebiet wird ohne Ruecksicht auf Gross-/Kleinschreibung erkannt",
       kriterien.passt(beispiel, lage))
lage = kriterien.laden({"kriterien": {"plz": ["10179"]}})
pruefe("PLZ statt Gebiet genuegt", kriterien.passt(beispiel, lage))
lage = kriterien.laden({"kriterien": {"plz_unklar": ["10179"]}})
pruefe("Grenz-PLZ wird gemeldet, aber markiert",
       kriterien.lage_status(beispiel, lage) == "unklar"
       and kriterien.passt(beispiel, lage))

ohne_wbs = kriterien.laden({"kriterien": {"wbs": "nein"}})
pruefe("Wohnung ohne WBS-Pflicht bleibt", kriterien.passt(beispiel, ohne_wbs))
pruefe("Wohnung mit WBS-Pflicht faellt raus",
       not kriterien.passt(dict(beispiel, wbs=True), ohne_wbs))
pruefe("unbekannte WBS-Pflicht faellt nicht raus",
       kriterien.passt(dict(beispiel, wbs=None), ohne_wbs))

pruefe("fehlender Preis laesst die Wohnung drin",
       kriterien.passt(dict(beispiel, rentNet=""), streng))

pruefe("Umgebungsvariable sticht config.json",
       kriterien.laden({"kriterien": {"max_kaltmiete": 900}},
                       umgebung='{"max_kaltmiete": 3000}')["max_kaltmiete"] == 3000)
try:
    kriterien.laden({}, umgebung="{kaputt")
    pruefe("kaputtes WBM_KRITERIEN wird bemaengelt", False)
except SystemExit as ende:
    pruefe("kaputtes WBM_KRITERIEN wird bemaengelt", "WBM_KRITERIEN" in str(ende))

# --- Leerer Bestand -------------------------------------------------------

leere_seite = lies("angebote.html").replace(
    'class="row openimmo-search-list-item"', 'class="row weg"'
).replace("13 Mietwohnungen", "0 Mietwohnungen")
try:
    pruefe("leerer Bestand ist kein Fehler", wbm.parse_list(leere_seite) == [])
except Exception:  # noqa: BLE001
    pruefe("leerer Bestand ist kein Fehler", False)

# --- Optional: die echte Seite -------------------------------------------

if "--live" in sys.argv:
    try:
        ids, by_id = wbm.collect()
        pruefe("echte Seite liefert Angebote", len(ids) > 0)
        pruefe("echte Angebote haben Titel und Link",
               all(by_id[i]["title"] and by_id[i]["deeplink"] for i in ids))
        eins = wbm.enrich(dict(by_id[ids[0]]))
        pruefe("Detailseite liefert die Kaltmiete", bool(eins["rentNet"]))
    except wbm.WbmError as fehler:
        pruefe("echte Seite erreichbar (%s)" % fehler, False)

print("Auswertung der WBM-Seite")
print("=" * 52)
fehler = 0
for beschreibung, ok in pruefungen:
    print("  [%s] %s" % ("OK" if ok else "FEHLER", beschreibung))
    fehler += 0 if ok else 1
print("=" * 52)
print("%d von %d bestanden" % (len(pruefungen) - fehler, len(pruefungen)))
sys.exit(1 if fehler else 0)
