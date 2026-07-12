# Claude Desktop Auto-Resume

**Nie pozwól, żeby 5-godzinny limit zatrzymał Claude'a w środku nocnej roboty.**

Kiedy w aplikacji **Claude Desktop** (Windows) skończy Ci się limit sesji, praca
staje w miejscu, dopóki ręcznie nie napiszesz „continue". Ten program pilnuje
okna Claude'a za Ciebie: wykrywa moment wyczerpania limitu, zapamiętuje godzinę
resetu i **minutę po resecie sam wpisuje „continue" i wciska Enter**. Rano
wracasz do skończonej pracy.

To odpowiednik popularnego linuksowego `claude-auto-retry`, ale dla **okienkowej
aplikacji Claude na Windows 11** (tamten działa tylko w terminalu na Linuksie).

![Zrzut ekranu aplikacji](docs/screenshot.png)

---

## Jak to działa — w skrócie

1. **Obserwuje okno Claude'a** co ~20 sekund (czyta interfejs tak, jak robi to
   czytnik ekranu — bez zrzutów ekranu, bez OCR).
2. **Wykrywa komunikat o limicie**, np. „You've used 100% of your … limit" albo
   „5-hour limit reached".
3. **Odczytuje godzinę resetu** z banera (np. „Resets Mon, Jul 13, 6:00 PM").
4. **Minutę po resecie** przełącza się na okno Claude'a, klika w pole czatu,
   pisze `continue` i wysyła. Potem sprawdza, czy limit faktycznie zniknął —
   jeśli nie, próbuje ponownie.

Dodatkowo, gdy czeka na reset, **nie pozwala komputerowi zasnąć**, żeby nocna
wysyłka na pewno się odbyła.

---

## Instalacja

### Czego potrzebujesz

- **Windows 10 lub 11**
- **Python 3.10+** — sprawdź w terminalu: `py --version`.
  Nie masz? Pobierz z [python.org](https://www.python.org/downloads/) i przy
  instalacji zaznacz „Add Python to PATH".
- Aplikacja **Claude Desktop** zainstalowana i zalogowana.

### Pobranie i uruchomienie

```powershell
git clone https://github.com/MichalCholajczyk/claude-desktop-auto-resume.git
cd claude-desktop-auto-resume
```

Następnie po prostu kliknij dwukrotnie **`run.bat`** — sam doinstaluje
potrzebną bibliotekę i uruchomi program.

Wolisz z ręki?

```powershell
py -m pip install --user uiautomation
py claude_auto_continue.py
```

---

## Pierwsze uruchomienie

1. Uruchom **Claude Desktop** i otwórz rozmowę, którą chcesz kontynuować.
2. Odpal ten program (`run.bat`). Zobaczysz ciemny panel z dużym zegarem.
3. U góry, w polu **„Okno Claude"**, zwykle od razu wykryte jest właściwe okno.
   Jeśli nie — kliknij **„Odśwież"**.
4. **Zanim zostawisz na noc, przetestuj wysyłkę raz**: kliknij
   **„Wyślij «continue» teraz"**. Jeśli w oknie Claude'a pojawi się i wyśle
   „continue" — wszystko działa.
5. Kliknij **„Rozpocznij czuwanie"** i zostaw program w tle.

Zielona lampka oznacza „czuwam". Gdy program wykryje limit, zegar zamieni się
w **odliczanie do wysyłki**, lampka zacznie mrugać na bursztynowo, a pasek pod
spodem pokaże, ile czekania zostało.

> **Nie udało się odczytać godziny resetu?** Wpisz ją ręcznie w polu
> **„Znasz godzinę resetu?"** (format `HH:MM`) i kliknij **„Uzbrój"** —
> program wyśle „continue" minutę po tej godzinie.

---

## ⚠️ Ważne przed zostawieniem na noc

- **Wyłącz automatyczne blokowanie ekranu.** Na zablokowanym pulpicie Windows
  nie pozwala symulować klawiatury, więc wysyłka się nie uda.
  (Ustawienia → Konta → Opcje logowania → „Wymagaj logowania: Nigdy", oraz
  wyłącz wygaszacz z ekranem logowania.)
- **W momencie wysyłki program na chwilę przejmuje klawiaturę** — fizycznie
  klika i pisze w oknie Claude'a. W nocy to bez znaczenia; w dzień po prostu
  nie pisz akurat w tej sekundzie na klawiaturze.
- **Zostaw otwartą tę rozmowę**, którą Claude ma kontynuować — program pisze
  w aktualnie widocznej sesji.

Zabezpieczenie: tuż przed wysyłką program sprawdza, czy okno Claude'a jest na
wierzchu. Jeśli nie zdoła go wysunąć, **nie pisze na ślepo** (nie wklei
„continue" do innej aplikacji), tylko spróbuje ponownie za 5 minut.

---

## Ustawienia

Dostępne w oknie programu; zapisują się do `auto_continue_config.json`:

| Opcja | Domyślnie | Co robi |
|---|---|---|
| Skanuj co (s) | 20 | jak często program zagląda do okna Claude'a |
| Wysyłaj automatycznie | ✔ | wyłącz, jeśli chcesz tylko sygnał dźwiękowy zamiast wysyłki |
| Nie usypiaj komputera | ✔ | blokuje uśpienie systemu i ekranu podczas czuwania |

Bardziej zaawansowane pola (treść wiadomości, liczba ponowień, dodatkowe wzorce
komunikatów o limicie) można zmienić bezpośrednio w pliku
`auto_continue_config.json` — opis w sekcji niżej.

---

## Rozwiązywanie problemów

**Nie widzi okna Claude'a.** Upewnij się, że Claude Desktop jest uruchomiony
(nie tylko w zasobniku), i kliknij „Odśwież".

**Wykrył limit, ale nie zna godziny resetu.** Anthropic mógł zmienić brzmienie
komunikatu. Program i tak spróbuje ponawiać co jakiś czas; możesz też podać
godzinę ręcznie („Uzbrój"). Jeśli chcesz nauczyć program nowego komunikatu,
dopisz wzorzec (wyrażenie regularne) do `extra_hard_patterns` w
`auto_continue_config.json`, np.:

```json
{ "extra_hard_patterns": ["nowy\\s+tekst\\s+o\\s+limicie"] }
```

**Podgląd na żywo w Dzienniku.** Okno „Dziennik" (oraz plik
`auto_continue.log`) pokazują dokładnie, co program widzi i robi — to pierwsze
miejsce, do którego warto zajrzeć, gdy coś nie gra.

---

## Prywatność i bezpieczeństwo

Program działa w całości **lokalnie na Twoim komputerze**. Czyta jedynie
interfejs okna Claude'a (przez systemowe API dostępności — to samo, którego
używają czytniki ekranu) i wpisuje jedno słowo: „continue". Nie wysyła żadnych
danych na zewnątrz, nie loguje treści rozmów, nie robi zrzutów ekranu.

---

## Jak to działa w środku (dla ciekawych)

- Interfejs okna (aplikacja Claude to Electron/Chromium) czytany jest przez
  **Windows UI Automation** (biblioteka `uiautomation`). Chromium buduje drzewo
  dostępności dopiero na żądanie, więc program najpierw je „budzi", odpytując
  dokumenty o `TextPattern`.
- **Treść rozmowy i pasek boczny są celowo pomijane** przy skanowaniu — dzięki
  temu rozmowa *o* limitach nie wywołuje fałszywego alarmu.
- Pole czatu to kontrolka o nazwie **Prompt**; kliknięcie w nią ustawia fokus,
  po czym `SendKeys("continue{Enter}")` wpisuje wiadomość.
- Logika stanów: `Czuwam → Limit (uzbrojony, znam czas wysyłki) → Wysyłka →
  Weryfikacja → Czuwam` (albo ponowienie, gdy limit dalej aktywny).

---

## Licencja

MIT — rób z tym, co chcesz.
