# WBM-Watcher

Prüft rund um die Uhr die Wohnungsangebote der [WBM](https://www.wbm.de/wohnungen-berlin/angebote/)
und schickt eine E-Mail, sobald eine Wohnung neu inseriert wird, die zu den
eigenen Kriterien passt.

Schwesterprojekt zum [inberlinwohnen-watcher](https://github.com/serenabis/inberlinwohnen-watcher):
gleiche Bedienung, gleicher Mailversand, gleicher Workflow – nur eine andere
Quelle. Die WBM ist in beiden Beständen **nicht** enthalten, es gibt also keine
doppelten Meldungen.

Läuft kostenlos in GitHub Actions – also unabhängig davon, ob der eigene Rechner
an ist. Kein Login bei der WBM nötig, keine externen Bibliotheken.

## Wie es funktioniert

Die WBM-Seite ist altmodisch und dadurch angenehm: Sie liefert **alle** Angebote
als fertiges HTML in einer einzigen Antwort. Kein JSON, keine
Nachlade-Komponenten, keine Paginierung – der Bestand ist mit gut einem Dutzend
Wohnungen klein genug, dass alles auf eine Seite passt.

Zwei Entscheidungen, die daraus folgen:

* **Gefiltert wird lokal, nicht auf dem Server.** Das Suchformular der WBM ist
  ein POST-Formular mit Sicherheits-Token (`__trustedProperties`, `__referrer`).
  Diese Token nachzubauen wäre brüchig und ginge bei jeder Formularänderung
  stillschweigend kaputt. Bei dieser Bestandsgröße kostet lokales Filtern
  nichts – und erlaubt Kriterien, die das Formular gar nicht anbietet:
  Kaltmiete, WBS-Pflicht, einzelne Postleitzahlen.
* **Detailseiten nur für neue Wohnungen.** Die Übersicht zeigt lediglich die
  Warmmiete. Kaltmiete, WBS-Pflicht, Etage und Bezugstermin stehen erst auf der
  Detailseite. Die wird ausschließlich für neue, grob passende Angebote geholt –
  im Regelfall also gar nicht.

Gemerkt wird die Objektnummer aus `data-id` (z. B. `1-5327/1/1`). Sie bleibt an
der Wohnung haften; die daneben stehende `data-uid` ist eine interne
Datenbank-ID und kann sich bei einem Neuimport ändern.

## Einrichtung

### 1. Repository anlegen

```bash
cd wbm-watcher
git init && git add -A && git commit -m "WBM-Watcher"
```

Dann auf github.com ein Repository anlegen und hochladen:

```bash
git remote add origin git@github.com:<DEIN-NAME>/wbm-watcher.git
git branch -M main && git push -u origin main
```

### 2. Postausgang wählen

Zum Versenden wird ein Mailkonto benötigt, das SMTP erlaubt. Empfangen wird an
die Adresse in `MAIL_TO` – das kann eine ganz andere sein.

| Anbieter   | `SMTP_HOST`                    | `SMTP_PORT` | Voraussetzung                                       |
|------------|--------------------------------|-------------|-----------------------------------------------------|
| web.de     | `smtp.web.de`                  | `587`       | In den Einstellungen „POP3/IMAP-Zugriff" aktivieren  |
| GMX        | `mail.gmx.net`                 | `587`       | dito                                                |
| Gmail      | `smtp.gmail.com`               | `587`       | 2FA an, dann ein App-Passwort erzeugen              |

Wenn beim inberlinwohnen-watcher bereits ein Konto funktioniert: dieselben Werte
verwenden.

### 3. Secrets hinterlegen

Im Repository unter **Settings → Secrets and variables → Actions → New
repository secret**:

| Name             | Beispiel                                 | Pflicht |
|------------------|------------------------------------------|---------|
| `SMTP_HOST`      | `smtp.web.de`                            | ja      |
| `SMTP_PORT`      | `587`                                     | nein (Vorgabe 587) |
| `SMTP_SSL`       | `1` für durchgehendes TLS                 | nein (automatisch bei Port 465) |
| `SMTP_USER`      | `dein-konto@web.de`                       | ja      |
| `SMTP_PASS`      | das App-Passwort                          | ja      |
| `MAIL_FROM`      | `dein-konto@web.de`                       | nein (Vorgabe = `SMTP_USER`) |
| `MAIL_TO`        | `wohin-die-meldungen-sollen@example.org`  | ja      |
| `WBM_KRITERIEN`  | die Suchkriterien als JSON (siehe unten)  | nein    |

`MAIL_TO` verträgt mehrere Adressen, durch Komma getrennt.

### 4. Starten

Unter **Actions → WBM-Watcher → Run workflow** einmal von Hand auslösen.

Der erste Lauf meldet **nicht** den gesamten Bestand, sondern merkt sich alle
aktuellen Angebote und schickt nur eine kurze Bestätigungsmail. Ab dann kommt
Post ausschließlich bei echten Neuzugängen.

## Suchkriterien

Die Kriterien stehen unter `kriterien` in `config.json`. Ab Werk ist **nichts**
eingeschränkt – der Watcher meldet also jede neue WBM-Wohnung.

```json
{
  "kriterien": {
    "bezirke_aus": ["Spandau"],
    "gebiete_mit_plz": {
      "Lichtenberg": {
        "ja": ["10365"],
        "unklar": ["10315", "10317", "10367", "10369"]
      }
    },
    "bezirke": [],
    "plz": [],
    "plz_unklar": [],
    "min_zimmer": null,
    "max_zimmer": null,
    "min_flaeche": null,
    "max_flaeche": null,
    "max_kaltmiete": 1400,
    "max_warmmiete": null,
    "wbs": "egal"
  }
}
```

Die Lage wird in vier Stufen geprüft: erst `bezirke_aus`, dann
`gebiete_mit_plz`, dann die Positivliste aus `bezirke`/`plz`, und wenn nichts
davon gesetzt ist, passt jede Lage.

| Feld | Bedeutung |
| --- | --- |
| `bezirke_aus` | Gebiete, die **nie** gemeldet werden. So lässt sich „überall, nur nicht dort" sagen, ohne alle erwünschten Gebiete aufzuzählen – eine solche Liste wäre unvollständig, sobald die WBM in einer bislang unbekannten Gegend baut. |
| `gebiete_mit_plz` | Grenzt einzelne Gebiete auf Ortsteile ein: dort zählt nur noch die Postleitzahl. `ja` meldet normal, `unklar` meldet mit dem Hinweis „Ortsteil prüfen". Für Bezirke, von denen nur ein Teil in Frage kommt. |
| `bezirke` | Gebiete, wie die WBM sie nennt – mal Bezirk, mal Ortsteil: `Mitte`, `Friedrichshain`, `Spandau`, `Lichtenberg` … Groß-/Kleinschreibung egal. Leer = jede Lage. |
| `plz` | Zusätzlich erlaubte Postleitzahlen – für Ortsteile, die im Gebietsnamen nicht auftauchen. |
| `plz_unklar` | Postleitzahlen auf Ortsteilgrenzen. Diese Wohnungen werden **gemeldet**, in der Mail aber mit „Ortsteil prüfen" markiert – lieber ein Inserat zu viel ansehen als eines verpassen. |
| `min_zimmer`, `max_zimmer` | Zimmerzahl |
| `min_flaeche`, `max_flaeche` | Wohnfläche in m² |
| `max_kaltmiete` | Nettokaltmiete in Euro (steht nur auf der Detailseite) |
| `max_warmmiete` | Warmmiete in Euro |
| `wbs` | `"egal"`, `"nein"` (nur ohne WBS-Pflicht) oder `"ja"` (nur mit) |

`null` oder eine leere Liste heißt jeweils „keine Einschränkung".

Ein Wert, der sich auf der Seite nicht lesen lässt, lässt die Wohnung **drin**:
ein fehlender Preis soll keine Wohnung verschwinden lassen, die sonst in Frage
käme.

Soll im öffentlichen Repository nicht stehen, wonach gesucht wird, bleibt der
Abschnitt in `config.json` leer und derselbe JSON-Block kommt stattdessen als
Secret `WBM_KRITERIEN` (lokal: Zeile `WBM_KRITERIEN={...}` in `.env`). Die
Umgebungsvariable sticht `config.json`.

## Wie schnell der Watcher reagiert

Der Engpass ist nicht die Website, sondern GitHub: `cron` ist eine Bitte, keine
Zusage. Geplante Läufe werden unter Last 10 bis 30 Minuten verzögert oder ganz
übersprungen.

Deshalb startet der Cron nur alle **30 Minuten** einen Job, und dieser Job prüft
**28 Minuten lang selbst weiter** (`watch.py --dauer 1680`):

* werktags 5–20 Uhr UTC: **alle 5 Minuten**
* sonst: alle 15 Minuten

Bewusst gemächlicher als beim Schwesterprojekt: Der gesamte WBM-Bestand sind
rund ein Dutzend Wohnungen, die sich ein paarmal pro Woche bewegen. Ein
Minutentakt würde daran nichts verbessern, sondern nur die fremde Seite
belasten. Ein Durchlauf ist eine einzige HTML-Seite; Detailseiten kommen nur
bei tatsächlicher Bewegung dazu.

Der Zustand wird während der Schleife nur in die Datei geschrieben; ins
Repository zurück schreibt ihn der Workflow einmal am Jobende – sonst entstünden
hunderte Commits am Tag. Die Taktweiten stehen als `TAKT_*` in `watch.py`;
deutlich unter 60 Sekunden sollte man nicht gehen, sonst wird aus regem Interesse
eine Belastung für die fremde Seite.

## Lokal ausprobieren

```bash
python3 watch.py --dry-run    # zeigt die Mail an, verschickt und speichert nichts

export SMTP_HOST=smtp.web.de SMTP_USER=... SMTP_PASS=... MAIL_TO=...
python3 watch.py --test-mail  # nur eine Testmail
python3 watch.py              # echter Lauf

python3 test_zugang.py             # Mailversand pruefen (fragt das Passwort ab)
python3 test_zugang.py --aus-env   # dasselbe mit den Werten aus .env
python3 test_konfiguration.py      # Zugangsdaten einlesen
python3 test_auswertung.py          # Auswertung gegen gespeicherte Seiten
python3 test_auswertung.py --live   # zusätzlich gegen die echte Seite
```

`test_auswertung.py` läuft gegen die Kopien in `testdaten/` und schlägt deshalb
genau dann fehl, wenn jemand den Parser kaputtmacht. Ob die **WBM** ihre Seite
umgebaut hat, verrät `--live`.

## Dateien

| Datei                            | Zweck                                              |
|----------------------------------|----------------------------------------------------|
| `wbm.py`                         | Abruf und Auswertung der Angebotsseite              |
| `test_zugang.py`                 | prueft Zugangsdaten mit einem Anmeldeversuch        |
| `kriterien.py`                   | Auswahl nach den eigenen Suchkriterien              |
| `watch.py`                       | Abgleich mit dem Stand, Mailversand                 |
| `config.json`                    | Suchkriterien                                       |
| `state/seen.json`                | bereits gemeldete Wohnungen                         |
| `testdaten/`                     | gespeicherte Beispielseiten für die Tests           |
| `.github/workflows/watch.yml`    | Zeitplan für GitHub Actions                         |

`state/seen.json` wird vom Workflow nach jedem Lauf ins Repository
zurückgeschrieben. Das ist zugleich praktisch, weil regelmäßige Commits
verhindern, dass GitHub den Zeitplan nach 60 Tagen Inaktivität abschaltet.

Schlägt der **Mailversand** fehl, bricht der Lauf sofort ab und der Job wird
**rot**. Das ist Absicht: Ein Anmeldefehler heilt nicht von selbst, und ein grün
gemeldeter Job, der nichts zustellen konnte, fällt monatelang niemandem auf.
Wiederholte Fehlanmeldungen im Minutentakt sind außerdem der schnellste Weg,
sich sein Mailkonto sperren zu lassen.

Einträge, die 90 Tage nicht mehr in den Angeboten auftauchten, werden vergessen –
die Datei wächst also nicht unbegrenzt. Bei Störungen (Seite nicht erreichbar,
Aufbau geändert) kommt höchstens alle 12 Stunden eine Warnmail.

## Hinweis

Die WBM behält sich in ihren Nutzungsbedingungen vor, dass die Angebote
ausschließlich zur **eigenen, persönlichen Immobiliensuche** verwendet werden –
nicht gewerblich und nicht zur Weitergabe an Dritte. Genau dafür ist dieser
Watcher gedacht: eine Person, ein Postfach, ein Abruf pro Minute zu Bürozeiten.
