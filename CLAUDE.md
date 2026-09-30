# CLAUDE.md

## Was dieses Repo ist

Projektarbeit von Nicolas Hunger und Lucas Weisshaar im **Modul 450 «Applikationen testen»**
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
  und Pull Request; die andere Person reviewt.
- API-Keys nie ins Repo. In automatischen Tests ist Bitget immer gemockt.
- Die Lehrperson bewertet KI-gestützte Projekte stärker nach Aufwand, und wir müssen den Code
  erklären können: Code erklären statt nur liefern und die KI-Nutzung im README nachführen.
- Beide sind Trading-Neulinge: Fachbegriffe (Kerze, Band, σ, Spot/Futures) kurz erklären.
