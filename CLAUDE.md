# CLAUDE.md

## Was dieses Repo ist

Projektarbeit von Nicolas Hunger, Lucas Weisshaar und Valentin Ernst im **Modul 450 «Applikationen testen»**
(TBZ, Klasse WUP25), Abgabe und Präsentation Mi 28.10.2026: ein Python-Trading-Bot, der mit der
Bollinger-Strategie auf dem Bitget-Demo-Konto handelt. Idee, Architektur, Teststrategie, Planung
und offene Entscheide stehen im `README.md`.

- GitHub: https://github.com/NicolasHunger/bollinger-bot (öffentlich)
- Lokal liegt dieses Repo eingebettet im Lernjournal-Repo `../../` (m450), das diesen Ordner per
  `.gitignore` ausschliesst. Dort liegen auch die übergeordnete `CLAUDE.md` und die
  Kursunterlagen unter `../../unterlagen/Unterlagen/` (Projekt: `projekt/`, Testkonzept:
  `testkonzept/`), online unter https://gitlab.com/ch-tbz-it/Stud/m450/m450.
  Nichts aus den Unterlagen ins Repo kopieren, nur verlinken.
- Diese Datei ist gitignored (wie im m450-Repo), also nur lokal.

## Entscheide aus der Projektfindung (23.09.2026)

- Nur **eine Strategie: Bollinger-Bänder**, σ geteilt durch n. RSI und «Bot-Duell» verworfen.
- **Polling-Dauerläufer:** Die ganze Logik steckt in `tick()`, eine dünne Schleife fragt jede
  Minute per REST. Kein WebSocket, weil das für Kerzen-Strategien nur Aufwand ohne Nutzen bringt.
- **Bitget nur im Demo-Modus** (Demo-API-Key, Header `paptrading: 1`), nie echtes Geld.
- Streamlit-Dashboard, Daten in SQLite.
- GitHub Actions (Build → Test → Deploy), SonarQube Cloud, Testreport auf GitHub Pages, Image in
  GHCR, Deploy per Docker Compose auf eine Cloud-VM.
- **Kein TDD:** bewusster Entscheid des Teams, obwohl die Eckdaten «TDD soll mal ausprobiert
  werden» verlangen und Doku und Präsentation eine Reflexion über TDD fordern.
- COBOL als Projektbasis verworfen: COBOL Check ist seit Mai 2026 archiviert und meldet
  Testfehler mit Exit-Code 0, Sonar analysiert COBOL nur in Enterprise-Plänen.

## Aufteilung und aktueller Stand (30.09.2026)

Die Paketnummern beziehen sich auf die Arbeitspakete-Tabelle im README.

| Person | Spur | Pakete |
|---|---|---|
| Lucas | Strategie und Ablauf | #8a.1 Grundgerüst, #1 Bollinger-Berechnung, #2 Kauf- und Verkaufsregeln, #6 `tick()`, Polling-Schleife und SQLite |
| Valentin | Börse | #3 Bitget-Client: Signatur und Kerzen, #4 Bitget-Client: Orders und Kontostand, #5 Risiko-Check |
| Nicolas | Infra und Dashboard | #8a.2 Pipeline (Build, Test, SonarQube Cloud), #8b Deploy auf die VM, #7 Dashboard |

- **Nicolas ist krank** und steigt später ein. Lucas und Valentin arbeiten bis dahin ohne ihn
  weiter. Seine Pakete nicht übernehmen, ausser er oder das Team sagt es ausdrücklich.
- **#8a ist darum geteilt:** #8a.1 Grundgerüst (Projektstruktur, `pyproject.toml`, pytest läuft
  lokal) macht Lucas, Valentin reviewt. #8a.2 Pipeline bleibt bei Nicolas, weil GitHub Secrets,
  Pages und SonarQube Cloud Admin-Rechte im Repo brauchen, die nur er hat.
- **#8a.1 muss zuerst gemergt sein.** Alle anderen Pakete bauen auf dem Grundgerüst auf. Wer
  vorher startet, legt keine eigene Projektstruktur an, sondern wartet auf diesen PR.
- **Bis #8a.2 steht, gibt es keine Pipeline:** vor jedem Merge `pytest` lokal laufen lassen.
- **Reviews im Ring:** Lucas reviewt Valentin, Nicolas reviewt Lucas, Valentin reviewt Nicolas.
  Solange Nicolas krank ist, reviewt Valentin die PRs von Lucas (#8a.1, #1, #2). #6 reviewt wieder
  Nicolas.
- **Gemeinsam festlegen, nicht allein entscheiden:** die Port-Schnittstelle zwischen `tick()`
  (Lucas) und dem `BitgetClient` (Valentin) sowie das SQLite-Schema zwischen `tick()` (Lucas) und
  dem Dashboard (Nicolas). Änderungen daran mit der anderen Person absprechen.
- **Nur im eigenen Paket arbeiten.** Fällt in einem fremden Paket etwas auf, im PR kommentieren
  statt selbst ändern.

## Bewertungsrubrik (Kurzfassung)

- Teststrategie kurz im README
- Unit-Tests **und** Integration-Tests, Schnittstellen mit einem Mocking-Framework weggemockt
- Testreports automatisch, Coverage als Report einsehbar (SonarQube Cloud)
- Tests laufen in der Pipeline beim Deploy auf `main` und werden reportet
- **3 Pull Requests pro Person**, aktiv kommentiert und gechallenged
- Doku: kurze Planung (ohne Gantt), Architektur visualisiert, kleines Testkonzept, Reflexion über
  TDD und Code Reviews, KI-Nutzung im README deklariert
- Präsentation 10 Minuten: Endprodukt, Testing, Reports, Reflexion, Fazit

## Konventionen

- Sprache Deutsch, Schweizer Schreibweise («ss» statt «ß»), auch in Commit-Messages.
- Laufend kleine, nachvollziehbare Commits. Jedes Arbeitspaket aus dem README als eigener Branch
  und Pull Request. Wer was macht, steht im Abschnitt «Aufteilung und aktueller Stand» unten.
- API-Keys nie ins Repo. In automatischen Tests ist Bitget immer gemockt.
- Die Lehrperson bewertet KI-gestützte Projekte stärker nach Aufwand, und wir müssen den Code
  erklären können: Code erklären statt nur liefern und die KI-Nutzung im README nachführen.
- Alle drei sind Trading-Neulinge: Fachbegriffe (Kerze, Band, σ, Spot/Futures) kurz erklären.
