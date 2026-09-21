# Dane z status.xml

Panel odczytuje lokalny plik aktualizowany przez zewnętrzny proces urządzenia.
Nie modyfikuje XML, nie wysyła poleceń do urządzenia i nie korzysta z snmp_conf.xml.

## Uruchomienie i migracja

W konfiguracji procesu (w instalacji Debian: `sudo amp-panel configure`) ustaw:

```ini
ENABLED_DEVICES=local,remote,oba,oba3
XML_STATUS_FILE=/var/lib/amp-panel/status.xml
XML_MAPPING_FILE=/usr/lib/amp-panel/app/devices/xml_mapping.json
XML_POLL_SECONDS=2
XML_STALE_SECONDS=60
```

W lokalnym projekcie domyślna ścieżka danych to `data/status.xml`, a mapowanie
to `app/devices/xml_mapping.json`. Można wskazać np. `D:/Downloads/status.xml`.
Zmienne należy przekazać procesowi serwera; sam moduł aplikacji nie ładuje `.env`.
Przykład PowerShell przed uruchomieniem serwera:

```powershell
$env:ENABLED_DEVICES = 'local,remote,oba,oba3'
$env:XML_STATUS_FILE = 'D:/Downloads/status.xml'
.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Pozostałe ustawienia logowania i bazy danych pozostają wymagane jak wcześniej.
Zmiana zmiennych procesu wymaga restartu usługi. Istniejąca instalacja zachowuje
swoje ENABLED_DEVICES: aby przejść z portu szeregowego, zastąp `amplifier,fts-ls`
czterema powyższymi identyfikatorami. Stara historia pozostaje w bazie; nowa
historia XML jest zapisywana osobno dla każdego widoku.

Próbkę dostarczonego pliku zapisano w `tests/fixtures/status.xml`.
To materiał testowy, nie źródło rzeczywistych bieżących pomiarów. Na potrzeby
krótkiego podglądu można skopiować go do `data/status.xml`. Bez procesu
aktualizującego ten plik panel po 60 sekundach oznaczy dane jako nieaktualne.

Proces produkujący XML powinien zapisywać kompletny dokument do pliku
tymczasowego i atomowo zastępować plik docelowy. Konto panelu potrzebuje prawa
odczytu. Pole **Last update** pokazuje czas modyfikacji pliku. Brak, uszkodzenie
lub nieaktualność pliku powoduje oznaczenie źródła jako rozłączonego. Ostatnie dane nie udają nowych.
Po naprawie pliku odczyt wznowi się automatycznie.

## Cztery widoki

| Widok | Sekcje XML |
|---|---|
| local | params_local, params_local_di |
| remote | params_remote, params_remote_di |
| oba | params_oba |
| oba3 | params_oba3 |

Widok wybiera się w rozwijanym menu **Amp Panel**. Każdy ma dane bieżące,
wykres wybranego pola oraz statystyki z ostatniej godziny. Brak sekcji DI jest
wyświetlany jako brak sekcji; nie oznacza awarii obecnego wariantu podstawowego.
Obecność sekcji w pliku oznacza tylko dostępność danych, nie potwierdza
fizycznego podłączenia modułu. Brak pola lub błędna wartość daje `--` i komunikat,
a nie sztuczne zero. Historia rejestruje obserwację przy każdym poprawnym odczycie.

## Zmiana nazw bez modyfikowania kodu strony

Edytuj `app/devices/xml_mapping.json`. Przykładowe pole:

```json
{
  "key": "Gain",
  "id": "5.1.1.1",
  "name": "Gain",
  "label": "Wzmocnienie rzeczywiste",
  "type": "number",
  "unit": ""
}
```

- `label`: nazwa widoczna w danych bieżących, wyborze wykresu i statystykach.
- `id`: identyfikator `param` w XML. Ma pierwszeństwo przed `name`.
- `name`: alternatywne dopasowanie po `<name>`, używane po usunięciu `id`.
- `key`: stały klucz aplikacji i historii. Nie zmieniaj go przy zmianie nazwy XML.
- `type`: `number` dla pomiarów, `text` dla tekstów (np. trybu pracy).
- `unit`: wyświetlana jednostka. Wpisz ją dopiero po potwierdzeniu w dokumentacji
  urządzenia; nie przelicza wartości.
- `xml_section`: nazwa sekcji XML; również można ją zmienić w mapowaniu.

Jeżeli producent zmieni tylko `<name>Gain</name>`, a pozostawi `id="5.1.1.1"`,
nic nie trzeba zmieniać. Gdy zmieni identyfikator, popraw `id` w mapowaniu.
Jeżeli chcesz dopasowywać po nazwie, usuń `id` i wpisz nową wartość `name`.
Nowe pole można dodać przez kolejny obiekt w `fields`, bez zmian w JS lub HTML.

Mapowanie jest wczytywane przy każdym odczycie; poprawne zmiany pojawią się
automatycznie. Nieprawidłowy JSON zgłosi błąd źródła i zostanie ponownie
sprawdzony przy następnym odczycie.

W instalacji docelowej warto skopiować mapowanie do
`/etc/amp-panel/xml_mapping.json` i wskazać je w `XML_MAPPING_FILE`, żeby lokalne
zmiany nazw przetrwały aktualizację pakietu. Plik musi być czytelny dla `amp-panel`.
