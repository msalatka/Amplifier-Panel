# Amp Panel

Amp Panel jest lokalną aplikacją webową do monitorowania i sterowania
urządzeniami optycznymi. Obsługuje cztery niezależne profile XML:

- `local` — stacja lokalna / DI,
- `remote` — stacja zdalna / DI,
- `oba` — wzmacniacz EDFA OBA,
- `oba3` — wzmacniacz EDFA OBA3.

Urządzenie przekazuje telemetrię przez `status.xml`. Panel nigdy nie modyfikuje
tego pliku. Polecenia w przeciwnym kierunku są publikowane w oddzielnym
`control.xml`, dzięki czemu odczyt i zapis nie konkurują o ten sam plik.

## Instalacja

### Budowanie pakietu Debiana

Na docelowym systemie Debian zainstaluj narzędzia budowania:

```bash
sudo apt update
sudo apt install build-essential debhelper git python3 python3-pip
```

W katalogu projektu uruchom:

```bash
./packaging/build_deb.sh
```

Pakiet `.deb` zostanie zapisany w katalogu nadrzędnym. Zainstaluj go, podając
rzeczywistą nazwę wygenerowanego pliku:

```bash
sudo apt install ../amp-panel_*.deb
```

Instalator tworzy użytkownika systemowego `amp-panel`, usługi systemd, katalog
danych oraz początkową konfigurację. Konfigurację można ponowić w dowolnym
momencie:

```bash
sudo amp-panel configure
```

Domyślnie polecenie otwiera pełny plik konfiguracyjny w `$VISUAL`, `$EDITOR`
lub systemowym `editor`. Kreator pytań można uruchomić przez:

```bash
sudo amp-panel configure --prompt
```

Po instalacji sprawdź system:

```bash
sudo amp-panel doctor
sudo amp-panel status
```

Panel jest dostępny domyślnie pod adresem:

```text
http://amp-panel.local:8000
```

Nazwa hosta i port zależą od konfiguracji instalacji.

## Korzystanie z aplikacji

Po zalogowaniu wybierz urządzenie z selektora w nagłówku. Każdy profil ma
niezależny stan połączenia, dane bieżące i historię.

### Live View

Pokazuje ostatni kompletny snapshot wybranego urządzenia. Dla wzmacniaczy
Administrator i Operator mogą wybierać pomiary widoczne na głównym ekranie.
Viewer ma dostęp wyłącznie do odczytu.

### Overview i Statistics

- **Overview** przedstawia przebieg wybranych wartości historycznych.
- **Statistics** oblicza statystyki dla wybranego okresu.
- Historia może zostać wyeksportowana do CSV.
- Układ wykresów jest wspólny dla użytkowników i zachowywany po restarcie.

### Administration

Funkcje administracyjne obejmują:

- **Access Control** — użytkownicy, role, hasła lokalne i aktywność kont,
- **SNMP Configuration** — agent, community oraz odbiorca trapów,
- **Network Configuration** — aktualny interfejs i ustawienia IPv4,
- **Time Diagnostics** — stan synchronizacji NTP,
- **Service Diagnostics** — telemetria XML, baza danych i Syslog,
- **Edit Variables** — edycja mapowania pól XML.

Zmiany sieciowe mogą przerwać bieżące połączenie z panelem. Należy wykonywać je
z interfejsu, którego konfiguracja jest aktualnie wyświetlana.

## Uwierzytelnianie

Panel obsługuje dwa tryby wybierane przez `amp-panel configure`:

- `local` — konta i hashe PBKDF2 są przechowywane lokalnie,
- `radius` — hasło sprawdza zewnętrzny serwer RADIUS, a panel przechowuje role i
  informację, czy konto jest aktywne.

W trybie RADIUS użytkownik musi istnieć zarówno w konfiguracji panelu, jak i na
serwerze RADIUS. Repozytorium zawiera pomocniczy instalator:

```bash
cd server_setup
sudo ./install_radius_server.sh
```

## Najważniejsza konfiguracja

Konfiguracja pakietu znajduje się w:

```text
/etc/amp-panel/amp-panel.env
```

Nie należy edytować jej podczas działania usługi. Bezpieczniej użyć:

```bash
sudo amp-panel configure
```

Najważniejsze ustawienia XML:

```ini
ENABLED_DEVICES=local,remote,oba,oba3
XML_STATUS_FILE=/var/lib/amp-panel/status.xml
XML_CONTROL_FILE=/var/lib/amp-panel/control.xml
XML_CONTROL_ACK_TIMEOUT_SECONDS=15
XML_MAPPING_FILE=/var/lib/amp-panel/xml_mapping.json
XML_POLL_SECONDS=2
XML_STALE_SECONDS=60
```

Pozostałe istotne ustawienia:

```ini
AMP_PANEL_PORT=8000
AMP_PANEL_DATA_DIR=/var/lib/amp-panel
DATABASE_FILE=/var/lib/amp-panel/measurements.db
PERSISTED_STATE_FILE=/var/lib/amp-panel/persisted_state.json
AUTH_MODE=local
SNMP_PORT=1161
```

Po zmianie konfiguracji `amp-panel configure` sprawdza wartości, przygotowuje
pliki i uprawnienia, a następnie restartuje usługi. Pliki bazy, stanu i
`control.xml` muszą znajdować się w `AMP_PANEL_DATA_DIR`.

## Telemetria: status.xml

`status.xml` jest własnością procesu urządzenia. Zalecany sposób aktualizacji to
zapis pliku tymczasowego i atomowe zastąpienie właściwego pliku. Panel cyklicznie
odczytuje dokument, ale nigdy go nie zapisuje.

Minimalna struktura:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<status>
  <module>
    <serial_number>DEVICE-001</serial_number>
    <firmware>1.0</firmware>
  </module>

  <params_oba3>
    <param id="5.1.1.1">
      <name>Gain</name>
      <value>30.0</value>
    </param>
    <param id="5.1.1.2">
      <name>GainSet</name>
      <value>28.5</value>
    </param>
  </params_oba3>
</status>
```

Sekcje używane przez domyślne profile:

| Profil | Sekcje XML |
|---|---|
| `local` | `params_local`, `params_localdi` |
| `remote` | `params_remote`, `params_remotedi` |
| `oba` | `params_oba` |
| `oba3` | `params_oba3` |

Plik musi być poprawnym XML UTF-8, mieć korzeń `<status>` i nie może przekraczać
1 MB. DTD oraz encje zewnętrzne są odrzucane. Brakujące, zduplikowane i
niepoprawne wartości są raportowane w diagnostyce profilu.

## Mapowanie pól XML

`xml_mapping.json` łączy elementy firmware z trwałymi kluczami aplikacji.
Przykładowe pole:

```json
{
  "key": "GainSet",
  "id": "5.1.1.2",
  "name": "GainSet",
  "label": "Gain setpoint",
  "type": "number",
  "unit": "dB",
  "role": "gain_set",
  "group": "Amplifier",
  "writable": true
}
```

Znaczenie właściwości:

- `key` — stabilny klucz używany przez API i historię,
- `id` lub `name` — selektor parametru w XML,
- `label`, `unit`, `group` — opis prezentowany użytkownikowi,
- `type` — `number` albo `text`,
- `role` — opcjonalna rola semantyczna,
- `writable` — jawne zezwolenie na zapis do `control.xml`,
- `minimum`, `maximum` — opcjonalne granice wartości zapisywanej.

Panel nigdy nie pozwala zapisać pola bez `"writable": true`. Zakresy należy
ustawić według specyfikacji urządzenia; aplikacja nie zgaduje bezpiecznych
wartości. Automatycznie odkryte pola są domyślnie tylko do odczytu.

Mapowanie można edytować w **Administration → Edit Variables**. Jest ono
wczytywane przy każdym odczycie XML, więc poprawna zmiana nie wymaga restartu.

## Sterowanie: control.xml

Żądanie sterujące jest walidowane według mapowania, otrzymuje UUID, a następnie
jest zapisywane atomowo. Przykład:

```xml
<?xml version="1.0" encoding="utf-8"?>
<control version="1">
  <request id="11111111-1111-4111-8111-111111111111"
           created_at="2026-09-25T14:30:00+00:00">
    <device id="oba3">
      <parameter section="oba3" key="GainSet" type="number"
                 id="5.1.1.2" name="GainSet">
        <value>28.5</value>
      </parameter>
    </device>
  </request>
</control>
```

Proces urządzenia powinien:

1. Obserwować `control.xml`.
2. Sprawdzić `request.id`.
3. Zastosować każde UUID najwyżej raz.
4. Zignorować ponownie odczytane, już obsłużone UUID.
5. Umieścić wynik w kolejnym `status.xml`.

Potwierdzenie w `status.xml`:

```xml
<control_status>
  <last_request_id>11111111-1111-4111-8111-111111111111</last_request_id>
  <state>applied</state>
  <message>OK</message>
</control_status>
```

Dozwolone stany odpowiedzi urządzenia:

- `pending` — urządzenie przyjęło żądanie,
- `applied` — zmiana została zastosowana,
- `rejected` — urządzenie odrzuciło wartość,
- `failed` — wykonanie zakończyło się błędem.

Jeżeli zgodne potwierdzenie nie pojawi się przed
`XML_CONTROL_ACK_TIMEOUT_SECONDS`, panel zwróci stan `timeout`.

## API sterowania XML

Zapis jest dostępny dla Administratora i Operatora:

```http
PUT /api/devices/oba3/control
Content-Type: application/json

{
  "values": {
    "oba3:GainSet": 28.5
  }
}
```

Odpowiedź zawiera `request_id`, stan `pending` i ścieżkę pliku sterującego.
Wartości są identyfikowane jako `sekcja:key`.

Stan ostatniego żądania:

```http
GET /api/devices/oba3/control/status
```

Każdy zapis jest rejestrowany w audycie. API odrzuca pola tylko do odczytu,
wartości niefinitywne, wartości poza skonfigurowanym zakresem i nadmiernie
długie teksty.

## Diagnostyka i obsługa usługi

```bash
sudo amp-panel status
sudo amp-panel doctor
sudo amp-panel logs -n 100
sudo amp-panel logs -f
sudo amp-panel restart
sudo amp-panel paths
```

`amp-panel doctor` sprawdza konfigurację, katalog danych, bazę SQLite,
`status.xml`, zapisywalność `control.xml` oraz usługi systemd.

Typowe problemy:

- **Brak danych** — sprawdź istnienie i czas modyfikacji `status.xml`.
- **Źródło stale** — proces urządzenia nie odświeżył pliku przed
  `XML_STALE_SECONDS`.
- **Control timeout** — urządzenie nie zwróciło zgodnego UUID w
  `<control_status>`.
- **Pole read-only** — w mapowaniu brakuje `"writable": true`.
- **Permission denied** — proces urządzenia i użytkownik `amp-panel` nie mają
  odpowiednich praw do katalogu wymiany.

## Rozwój i testy

Instalacja zależności Pythona:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

Uruchomienie testów:

```bash
python -m unittest discover -s tests -q
```

Kontrola kodu Pythona:

```bash
python -m ruff check .
python -m ruff format --check .
```

Frontend korzysta z lokalnie dołączonego Chart.js, dlatego wykresy nie
wymagają dostępu do Internetu.
