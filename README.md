# Amp Panel

Amp Panel jest lokalną aplikacją webową, która odczytuje stan urządzeń i
przekazuje do nich polecenia za pomocą plików XML. Obsługuje cztery profile:

- `local` — stacja lokalna / DI,
- `remote` — stacja zdalna / DI,
- `oba` — wzmacniacz EDFA OBA,
- `oba3` — wzmacniacz EDFA OBA3.

Panel odczytuje bieżące dane urządzenia z pliku 'status.xml', a polecenia i 
ustawienia zapisuje w osobnym pliku 'control.xml'

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
nazwę wygenerowanego pliku:

```bash
sudo apt install ../amp-panel_*.deb
```

Instalator tworzy użytkownika systemowego `amp-panel`, usługi systemd, katalog
danych oraz wstępną konfigurację. Konfigurację można zmienić w dowolnym
momencie komendą:

```bash
sudo amp-panel configure
```

Polecenie otwiera pełny plik konfiguracyjny w `$VISUAL`, `$EDITOR` lub
systemowym `editor`, a przed zastosowaniem zmian sprawdza jego poprawność.

Po instalacji można sprawdzić działanie systemu:

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

### Control

Zakładka **Control** jest dostępna dla Operatora i Administratora. Jej pola są
tworzone automatycznie na podstawie `xml_mapping.json`: pojawia się w niej każde
pole oznaczone jako `"writable": true`. Po wybraniu **Apply changes** panel
zapisuje do `control.xml` wyłącznie wartości zmienione przez użytkownika.

Sekcja **Last request** pokazuje identyfikator i stan ostatniego polecenia.
Urządzenie potwierdza wykonanie w `status.xml`; panel prezentuje następujące stany:
`pending`, `applied`, `rejected`, `failed` i `timeout`. Dodanie kolejnego pola
sterującego nie wymaga zmiany kodu GUI — wystarczy dodać je do mapowania wraz z
typem, zakresem i flagą `writable`.

### Overview i Statistics

- **Overview** przedstawia wykres wartości historycznych z wybranego okresu czasu.
- **Statistics** oblicza statystyki dla wybranego okresu.
- Historia może zostać wyeksportowana do CSV.

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
  "writable": true,
  "minimum": 0,
  "maximum": 40,
  "alarm": {
    "enabled": true,
    "minimum": 10,
    "maximum": 35
  }
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
- `alarm.enabled` — włącza sprawdzanie progów dla wartości odczytanej,
- `alarm.minimum`, `alarm.maximum` — niezależne granice alarmowe.

Blok `alarm` jest opcjonalny. Jego brak oznacza alarm wyłączony. Po zapisaniu
zakładki **Warnings** aplikacja nie dopisuje pustego `{"enabled": false}`: jeśli
alarm jest wyłączony i oba progi są puste, istniejący blok zostaje usunięty z
mapowania. Wyłączony alarm z wpisanym minimum lub maksimum pozostaje zapisany,
aby można go było później ponownie włączyć bez utraty progów.

Panel nigdy nie pozwala zapisać pola bez `"writable": true`. Zakresy należy
ustawić według specyfikacji urządzenia; aplikacja nie zgaduje bezpiecznych
wartości. Automatycznie odkryte pola są domyślnie tylko do odczytu.

Mapowanie można edytować w **Administration → Edit Variables**. Jest ono
wczytywane przy każdym odczycie XML, więc poprawna zmiana nie wymaga restartu.

## Alarmy i trapy SNMP

Zakładka **Warnings** pokazuje aktywne przekroczenia i pozwala Operatorowi lub
Administratorowi konfigurować alarmy dla wszystkich pól liczbowych. Konfiguracja
jest zapisywana bezpośrednio w `xml_mapping.json`; nie istnieje drugi plik progów.
Można ustawić tylko dolną granicę, tylko górną albo obie.

Alarm jest otwierany tylko przy przejściu wartości poza zakres i zamykany po jej
powrocie. Zdarzenia `OPEN` oraz `CLEARED` trafiają do Sysloga. Przy `OPEN` panel
wysyła jeden trap SNMP na skonfigurowany adres. Powtarzające się odczyty tej
samej nieprawidłowej wartości nie generują kolejnych trapów.

Alarm pozostaje widoczny do potwierdzenia przez Operatora lub Administratora.
Potwierdzenie aktywnego alarmu nie ukrywa go; wpis znika dopiero po jednoczesnym
potwierdzeniu i powrocie wartości do prawidłowego zakresu. Dzięki temu krótki
alarm, który ustąpił przed otwarciem strony, nadal wymaga świadomego
potwierdzenia. Funkcji ignorowania alarmów nie ma.

W **SNMP Configuration** przycisk **Send test trap** wysyła kontrolny trap bez
konieczności wywołania rzeczywistego alarmu.

Granice `minimum` i `maximum` na poziomie pola dotyczą wartości wysyłanej przez
`control.xml`. Granice wewnątrz `alarm` dotyczą wyłącznie telemetrii odczytanej
z `status.xml`; te dwa mechanizmy są celowo rozdzielone.

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

## Dane historyczne w SQLite

SQLite nie przechowuje już osobnych kolumn dla `GainSet`, temperatury ani innych
wyróżnionych parametrów. Tabela `device_snapshots` zapisuje dla każdego profilu
czas obserwacji, identyfikator profilu oraz kompletny, zwalidowany snapshot XML
w postaci JSON. Nowy rekord powstaje tylko wtedy, gdy wartości danego urządzenia
ulegną zmianie; samo ponowne zapisanie identycznego `status.xml` nie powiększa
historii.

Tabela `device_hourly_statistics` zawiera godzinowe podsumowania liczbowe używane
do szybkiego wyświetlania długich zakresów. Konfiguracja alarmów pozostaje w
`xml_mapping.json`, aktywne alarmy są stanem bieżącego procesu, a trwała historia
otwarć i zamknięć jest zapisywana w Syslogu — nie w SQLite.

`GainSet` nie ma specjalnego magazynu. Aktualna wartość pochodzi z `status.xml`,
żądana wartość jest publikowana w `control.xml`, a historia — tak jak dla innych
pól — znajduje się w snapshotach SQLite.

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
