# DBS Dahua Access

**Autor:** Digital Best Solutions  
**Typ projektu:** Home Assistant Custom Integration / HACS  
**Robocza nazwa domeny integracji:** `dbs_dahua_access`  
**Docelowa platforma:** Home Assistant OS / Home Assistant Core  
**Pierwszy wspierany kontroler:** Dahua DHI-ASC2204B-S  

---

## 1. Cel projektu

`DBS Dahua Access` ma być własną integracją Home Assistant przeznaczoną do lokalnej obsługi kontrolerów dostępu Dahua.

Główne cele:

- zdalny podgląd stanu kontrolerów i przejść,
- ręczne otwieranie drzwi z Home Assistant,
- czytelny podgląd ostatnich zdarzeń,
- wystawianie eventów Home Assistant po zdarzeniach z kontroli dostępu,
- możliwość tworzenia automatyzacji na podstawie użytej karty, użytkownika, czytnika, drzwi i wyniku autoryzacji,
- działanie lokalne, bez zależności od chmury Dahua,
- obsługa wielu kontrolerów jednocześnie,
- instalacja i aktualizacja przez HACS.

Przykładowe zastosowanie:

> Użytkownik używa karty przy czytniku w piwnicy. Kontroler przyznaje dostęp. Integracja wystawia event w Home Assistant, a automatyzacja włącza światło w piwnicy na określony czas.

---

## 2. Założenia

Integracja ma komunikować się bezpośrednio z kontrolerami Dahua po sieci LAN.

Przykładowe kontrolery:

```text
10.10.20.30:37777
10.10.20.31:37777
10.10.20.32:37777
10.10.20.33:37777
```

Sieć kontroli dostępu może znajdować się w osobnym VLAN-ie, np.:

```text
VLAN 20 – SECURITY
10.10.20.0/24
```

Home Assistant może znajdować się w osobnym VLAN-ie, np.:

```text
VLAN 30 – SERVERS
10.10.30.0/24
```

ACL powinien zezwalać Home Assistantowi na inicjowanie połączeń do kontrolerów Dahua.

---

## 3. Zakres wersji 1.0

### 3.1. Dodawanie kontrolera

Kontroler dodawany z GUI Home Assistant przez `Config Flow`.

Pola:

- nazwa urządzenia,
- adres IP / hostname,
- port Dahua,
- login,
- hasło,
- opcjonalnie port HTTP,
- opcjonalnie weryfikacja SSL, jeżeli urządzenie obsługuje HTTPS.

Integracja powinna przed zapisaniem konfiguracji:

1. sprawdzić połączenie,
2. zweryfikować poświadczenia,
3. pobrać model i numer seryjny urządzenia,
4. wykryć liczbę dostępnych przejść.

Hasło nie może być logowane ani zapisywane do logów diagnostycznych.

---

## 4. Model urządzeń w Home Assistant

Każdy fizyczny kontroler powinien być widoczny jako osobne `Device`.

Przykład:

```text
Device:
  Name: KD1 PIWNICA KOZLA
  Manufacturer: Dahua
  Model: DHI-ASC2204B-S
  IP: 10.10.20.30
```

Pod urządzeniem powinny znajdować się encje odpowiadające jego drzwiom oraz stanowi kontrolera.

---

## 5. Encje

### 5.1. Drzwi

Preferowany typ encji:

```text
button
```

dla operacji chwilowej `Open Door`.

Przykłady:

```text
button.kd1_door_1_open
button.kd1_door_2_open
button.kd1_door_3_open
button.kd1_door_4_open
```

Jeżeli kontroler zwraca wiarygodny fizyczny stan drzwi lub zamka, można dodatkowo wystawić:

```text
binary_sensor.kd1_door_1
binary_sensor.kd1_door_2
```

Przykładowe stany:

```text
closed
open
forced_open
held_open
unknown
```

Nie należy używać encji `lock`, jeżeli urządzenie nie udostępnia wiarygodnego, aktualnego stanu ryglowania.

---

### 5.2. Stan kontrolera

Przykładowe encje:

```text
binary_sensor.kd1_online
sensor.kd1_last_event
sensor.kd1_last_user
sensor.kd1_last_card
sensor.kd1_last_door
sensor.kd1_last_result
```

Opcjonalnie:

```text
sensor.kd1_firmware
sensor.kd1_device_time
```

---

## 6. Zdarzenia Home Assistant

Najważniejszą częścią integracji ma być wystawianie natywnych eventów Home Assistant.

Nazwa eventu:

```text
dbs_dahua_access_event
```

Przykładowy event po poprawnej autoryzacji:

```yaml
event_type: dbs_dahua_access_event
data:
  controller: KD1 PIWNICA KOZLA
  controller_ip: 10.10.20.30
  door: 3
  door_name: PIWNICA
  reader: 1
  event: access_granted
  user_id: "12"
  user_name: "Rafal"
  card_number: "05883EDA"
  method: card
  timestamp: "2026-09-19T19:30:00+02:00"
```

Przykład odmowy:

```yaml
event_type: dbs_dahua_access_event
data:
  controller: KD1 PIWNICA KOZLA
  door: 3
  event: access_denied
  card_number: "01234567"
  reason: no_permission
```

---

## 7. Typy eventów

Integracja powinna, jeśli urządzenie je udostępnia, normalizować zdarzenia Dahua do czytelnych nazw.

Minimalny zestaw:

```text
access_granted
access_denied
door_opened
door_closed
door_forced_open
door_held_open
remote_open
card_read
pin_used
controller_online
controller_offline
tamper
```

Dodatkowo zachować surowy kod zdarzenia Dahua jako pole:

```yaml
raw_event_code: AccessControl
```

---

## 8. Automatyzacje

Integracja ma umożliwiać automatyzacje bez dodatkowego middleware.

### Przykład: światło w piwnicy po poprawnym wejściu

```yaml
alias: Piwnica - światło po dostępie
trigger:
  - platform: event
    event_type: dbs_dahua_access_event
    event_data:
      event: access_granted
      door_name: PIWNICA

action:
  - service: light.turn_on
    target:
      entity_id: light.piwnica

  - delay: "00:10:00"

  - service: light.turn_off
    target:
      entity_id: light.piwnica
```

### Przykład: automatyzacja tylko dla konkretnej karty

```yaml
trigger:
  - platform: event
    event_type: dbs_dahua_access_event

condition:
  - condition: template
    value_template: >
      {{ trigger.event.data.card_number == "05883EDA" }}
```

---

## 9. Logi

Integracja powinna prowadzić czytelne logi Home Assistant.

Przykład:

```text
DBS Dahua Access: KD1 / PIWNICA / Access granted / Rafal / card 05883EDA
```

Nie logować:

- haseł,
- pełnych danych uwierzytelniających,
- tokenów sesyjnych.

Opcjonalny tryb `debug` powinien umożliwiać zapis surowych ramek/protokołu potrzebnych podczas developmentu, ale bez danych wrażliwych.

---

## 10. Komunikacja z Dahua

Warstwa komunikacji powinna być oddzielona od kodu Home Assistant.

### 10.1. Główny protokół dla DHI-ASC2204B-S

Dla kontrolerów `DHI-ASC2204B-S` podstawowym i preferowanym kanałem komunikacji jest natywny protokół Dahua obsługiwany przez **Dahua NetSDK po TCP 37777**.

Zweryfikowane środowisko testowe:

```text
10.10.20.30:37777  OPEN
10.10.20.30:5000   CLOSED
10.10.20.30:80     CLOSED
```

Wniosek projektowy:

```text
ASC2204B-S
   ↓
Dahua NetSDK / native protocol
TCP 37777
   ↓
DBS Dahua Access
   ↓
Home Assistant
```

Integracja nie powinna wymagać chmury Dahua. Komunikacja ma odbywać się lokalnie między Home Assistantem a kontrolerem.

Podstawowe operacje realizowane przez NetSDK:

- logowanie i uwierzytelnianie,
- odczyt informacji o urządzeniu,
- odczyt stanu przejść,
- zdalne otwieranie drzwi,
- subskrypcja zdarzeń kontroli dostępu,
- odbiór informacji o karcie/użytkowniku/wyniku autoryzacji,
- automatyczny reconnect po utracie komunikacji.

HTTP/CGI oraz DHIP na porcie 5000 należy traktować wyłącznie jako **opcjonalne capability/fallback** dla innych modeli Dahua, jeśli dany kontroler je udostępnia. Dla `ASC2204B-S` nie są one podstawą implementacji.


Proponowana struktura:

```text
custom_components/dbs_dahua_access/
├── __init__.py
├── manifest.json
├── config_flow.py
├── const.py
├── coordinator.py
├── button.py
├── binary_sensor.py
├── sensor.py
├── diagnostics.py
├── strings.json
├── translations/
│   ├── en.json
│   └── pl.json
└── dahua/
    ├── __init__.py
    ├── client.py
    ├── events.py
    ├── protocol.py
    └── models.py
```

`dahua/client.py` odpowiada za:

- logowanie,
- połączenie,
- ponowne połączenie,
- wysyłanie komendy otwarcia drzwi,
- pobieranie informacji o urządzeniu.

`dahua/events.py` odpowiada za:

- utrzymanie strumienia eventów,
- dekodowanie zdarzeń,
- mapowanie surowych zdarzeń Dahua na format integracji.

---

## 11. Reconnect i odporność na awarie

Integracja powinna:

- automatycznie odnawiać połączenie po utracie komunikacji,
- stosować rozsądny backoff,
- nie generować tysięcy błędów w logach przy niedostępnym kontrolerze,
- oznaczać encje jako `unavailable`,
- po powrocie urządzenia automatycznie wznowić subskrypcję eventów.

Przykładowy backoff:

```text
5 s
10 s
30 s
60 s
max 300 s
```

---

## 12. Bezpieczeństwo

Założenia:

- komunikacja tylko lokalna,
- kontrolery nie muszą być wystawiane do Internetu,
- Home Assistant powinien komunikować się z VLAN-em SECURITY przez ACL,
- dane logowania przechowywane przez mechanizmy Home Assistant,
- brak haseł w logach,
- brak domyślnego automatycznego otwierania drzwi po restarcie,
- każda akcja ręcznego otwarcia powinna być logowana.

Opcjonalnie można później dodać:

```text
Require confirmation before opening
```

dla wybranych przejść.

---

## 13. Nazewnictwo encji

Przykład dla kontrolera:

```text
KD1 PIWNICA KOZLA
```

Encje:

```text
button.kd1_piwnica_kozla_door_1_open
button.kd1_piwnica_kozla_door_2_open
binary_sensor.kd1_piwnica_kozla_door_1
sensor.kd1_piwnica_kozla_last_user
sensor.kd1_piwnica_kozla_last_card
```

Nazwy drzwi powinny, jeśli to możliwe, zostać pobrane z kontrolera lub konfiguracji użytkownika.

Docelowy model nazw:

```text
Device w Home Assistant: KD1 PIWNICA KOZLA
Encja: KD1 PIWNICA KOZLA · <nazwa drzwi/przejścia>
```

Numer drzwi/kanału (`door=1`, `door=2`) jest szczegółem technicznym i powinien być zachowany w atrybutach encji/eventu, ale nie powinien być główną nazwą widoczną dla użytkownika.

Przykład:

```yaml
device:
  name: KD1 PIWNICA KOZLA
entity:
  name: KD1 PIWNICA KOZLA · Magazyn wejście
  attributes:
    door: 2
    door_label: Magazyn wejście
```

### 13.1. Wynik testu nazw drzwi / przejść w labie

Dodane narzędzie:

```text
tools/dahua_access_names_probe.py
```

Wyniki na `KD1 PIWNICA KOZLA`:

- `OperateAccessControlManager(... GETSUBCONTROLLER_INFO ...)` zwraca błąd `The device does not support current operation`, więc ten model nie oddaje w ten sposób listy podkontrolerów / przejść.
- `GetNewDevConfig("AccessControl", channel=0..3)` działa i zwraca konfigurację drzwi/kanału, ale w odczytanym JSON nie widać czytelnego pola nazwy drzwi.
- `GetNewDevConfig("AccessControlGeneral", ...)` działa i zwraca m.in. `AccessProperty` oraz `ABLock`.
- `GetNewDevConfig("AccessEvent", ...)`, `AccessControlDoor`, `AccessDoor`, `Door` nie są obsługiwane na tym urządzeniu.

Najbardziej obiecujący trop dla nazwy drzwi jest w samym evencie NetSDK:

```text
NET_A_ALARM_ACCESS_CTL_EVENT_INFO.szDoorName
```

Pole jest opisane w SDK jako `Entrance Guard Name`. Webowy lab odczytuje je teraz jako:

```yaml
door_name: "..."
```

Do potwierdzenia po kolejnym odbiciu karty: czy `KD1` realnie wypełnia `szDoorName`, czy zostawia je puste. Jeżeli pole będzie puste, nazwy drzwi trzeba będzie brać z konfiguracji użytkownika integracji Home Assistant.

Webowy lab obsługuje teraz fallbacki lokalne:

```text
DAHUA_DOOR_1_NAME=...
DAHUA_DOOR_2_NAME=...
DAHUA_DOOR_NAMES=1=Wejście,2=Magazyn
```

Kolejność wyboru nazwy:

1. `szDoorName` z eventu kontrolera,
2. lokalna konfiguracja `DAHUA_DOOR_<nr>_NAME` / `DAHUA_DOOR_NAMES`,
3. fallback `Przejście <nr>`.

---

## 14. Konfiguracja wielu kontrolerów

Integracja musi obsługiwać wiele wpisów `Config Entry`.

Przykład:

```text
KD1  10.10.20.30
KD2  10.10.20.31
KD3  10.10.20.32
KD4  10.10.20.33
```

Każdy kontroler:

- osobny wpis konfiguracji,
- osobny `Device`,
- osobny lifecycle połączenia,
- wspólny kod biblioteki.

Awaria jednego kontrolera nie może wpływać na pozostałe.

---

## 15. HACS

Repozytorium ma być zgodne z HACS.

Minimalne pliki:

```text
hacs.json
README.md
LICENSE
custom_components/dbs_dahua_access/
```

Przykładowy `hacs.json`:

```json
{
  "name": "DBS Dahua Access",
  "render_readme": true,
  "homeassistant": "2026.1.0"
}
```

Repozytorium powinno być dodawane do HACS jako:

```text
Integration
```

---

## 16. manifest.json

Przykładowy szkielet:

```json
{
  "domain": "dbs_dahua_access",
  "name": "DBS Dahua Access",
  "codeowners": [],
  "config_flow": true,
  "documentation": "https://github.com/OWNER/dbs-dahua-access",
  "integration_type": "hub",
  "iot_class": "local_push",
  "requirements": [],
  "version": "0.1.0"
}
```

Docelowo `iot_class` powinno pozostać:

```text
local_push
```

jeżeli kontroler wysyła zdarzenia w czasie rzeczywistym.

---

## 17. Pierwszy etap developmentu

Dla `DHI-ASC2204B-S` bazowym transportem jest **Dahua NetSDK po TCP 37777**.

Zweryfikowany kontroler testowy:

```text
10.10.20.30:37777
```

Pierwszy proof-of-concept powinien wykonać następujące kroki:

1. połączyć się z kontrolerem przez NetSDK,
2. wykonać login z użyciem lokalnego konta urządzenia,
3. pobrać model, numer seryjny i podstawowe informacje o urządzeniu,
4. sprawdzić liczbę dostępnych kanałów/przejść,
5. odczytać stan co najmniej jednego przejścia,
6. zasubskrybować zdarzenia kontroli dostępu,
7. potwierdzić odbiór zdarzenia po użyciu karty/PIN-u,
8. sprawdzić, czy event zawiera:
   - kontroler,
   - drzwi/kanał,
   - czytnik, jeśli urządzenie rozróżnia stronę,
   - numer karty,
   - UserID,
   - metodę uwierzytelnienia,
   - wynik autoryzacji,
9. dopiero po testach odczytu wykonać kontrolowany test `Open Door`,
10. sprawdzić zachowanie po zerwaniu połączenia i ponownym połączeniu.

HTTP/CGI oraz DHIP/5000 mogą być sprawdzane dodatkowo w ramach autodetekcji capability dla innych modeli, ale nie są wymagane dla `ASC2204B-S`.

Dopiero po potwierdzeniu stabilnej komunikacji NetSDK implementować właściwe encje Home Assistant.

---

## 18. Etapy wdrożenia

### MVP 0.1

- konfiguracja kontrolera z GUI,
- połączenie z jednym `ASC2204B-S` przez NetSDK/TCP 37777,
- odczyt informacji o urządzeniu,
- ręczne otwieranie drzwi,
- sensor online/offline,
- odbiór eventów karty/PIN-u z NetSDK,
- event `dbs_dahua_access_event`,
- automatyczny reconnect.

### 0.2

- wiele kontrolerów,
- automatyczny reconnect,
- nazwy drzwi,
- sensory ostatniego użytkownika/karty,
- polskie tłumaczenie.

### 0.3

- stan fizyczny drzwi,
- alarm wymuszonego otwarcia,
- tamper,
- diagnostyka,
- lepsze logowanie.

### 1.0

- stabilne API,
- pełna obsługa wielu kontrolerów,
- HACS,
- dokumentacja,
- testy,
- mechanizm migracji konfiguracji między wersjami.

---

## 19. Możliwe rozszerzenia

W przyszłości:

- integracja z kamerami Dahua,
- automatyczny snapshot przy wejściu,
- encja ostatniego zdjęcia dla zdarzenia,
- logowanie wejść do osobnego panelu Lovelace,
- historia zdarzeń per drzwi,
- mapowanie numerów kart na użytkowników HA,
- automatyczne alarmy przy próbach dostępu,
- możliwość czasowego odblokowania drzwi,
- tryb `Always Open`,
- integracja z powiadomieniami mobilnymi,
- webhook/MQTT jako opcjonalny output dla systemów zewnętrznych.

---

## 20. Założenie projektowe

SmartPSS pozostaje podstawowym narzędziem do:

- zarządzania użytkownikami,
- kartami,
- PIN-ami,
- grupami dostępu,
- harmonogramami.

`DBS Dahua Access` nie ma w pierwszej wersji zastępować SmartPSS.

Integracja ma zapewnić przede wszystkim:

```text
monitoring
+
sterowanie
+
eventy
+
automatyzacje
```

czyli funkcje, których brakuje w codziennym użyciu systemu Dahua.

---

## 21. Autor

**Digital Best Solutions**

Projekt tworzony jako niezależna integracja Home Assistant dla lokalnych systemów kontroli dostępu Dahua.

Nazwa robocza:

# DBS Dahua Access

Domena Home Assistant:

```text
dbs_dahua_access
```

---

## 22. Integracja z natywnymi tagami Home Assistant

Podczas testów NetSDK z kontrolerem `DHI-ASC2204B-S` potwierdzono, że zdarzenia kontroli dostępu zawierają numer karty:

```text
card_number: 058D1C32
user_id: 8
reader: 3
door: 2
open_method: card
result: access_granted
```

Wniosek projektowy:

```text
Dahua card_number
   ↓
DBS Dahua Access
   ↓
Home Assistant tag / RFID / NFC identity
```

Home Assistant posiada natywny mechanizm `Tags`, który pozwala automatyzować akcje na podstawie identyfikatora taga. Oficjalna dokumentacja HA opisuje event:

```text
tag_scanned
```

z danymi:

```yaml
tag_id: "..."
name: "..."
device_id: "..."
```

Integracja `DBS Dahua Access` powinna rozważyć opcjonalny tryb zgodności z natywnymi tagami HA:

- karta Dahua może być traktowana jako `tag_id`,
- czytnik lub kontroler może pełnić rolę urządzenia skanującego,
- event Dahua `access_granted` może opcjonalnie generować także event `tag_scanned`,
- natywny panel tagów HA może być używany do rozpoznawania kart i budowania automatyzacji bez ręcznego mapowania numerów kart w YAML.

Nie powinno to zastępować głównego eventu integracji:

```text
dbs_dahua_access_event
```

lecz działać jako dodatkowa warstwa kompatybilności.

### 22.1. Proponowane mapowanie

Minimalne mapowanie:

```yaml
tag_id: "058D1C32"
device_id: "<device_id kontrolera lub czytnika>"
```

Opcjonalnie, aby uniknąć kolizji z innymi tagami NFC/RFID w HA:

```yaml
tag_id: "dahua:058D1C32"
```

lub w trybie prywatności:

```yaml
tag_id: "dahua:<hash numeru karty>"
```

Pole `device_id` powinno, jeśli technicznie możliwe, wskazywać urządzenie Home Assistant odpowiadające kontrolerowi lub czytnikowi, który odczytał kartę.

### 22.2. Tryby konfiguracji

Opcja w konfiguracji integracji:

```text
Enable Home Assistant tag compatibility
```

Możliwe warianty:

```text
off
raw_card_number
prefixed_card_number
hashed_card_number
```

Domyślnie tryb powinien być wyłączony albo używać prefiksu:

```text
dahua:<card_number>
```

żeby uniknąć przypadkowych kolizji z innymi tagami w systemie.

### 22.3. Przykład automatyzacji natywnej HA

Przykład automatyzacji na podstawie natywnego eventu tagów:

```yaml
alias: Piwnica - karta Dahua jako tag HA
trigger:
  - platform: event
    event_type: tag_scanned
    event_data:
      tag_id: "dahua:058D1C32"

action:
  - service: light.turn_on
    target:
      entity_id: light.piwnica
```

Równolegle integracja nadal powinna emitować pełny event domenowy:

```yaml
event_type: dbs_dahua_access_event
data:
  event: access_granted
  card_number: "058D1C32"
  user_id: "8"
  reader: 3
  door: 2
  method: card
```

### 22.4. Bezpieczeństwo i prywatność

Numery kart mogą być traktowane jako dane wrażliwe.

Wymagania:

- nie logować numerów kart w trybie innym niż debug, jeśli użytkownik wyłączy jawne logowanie kart,
- umożliwić maskowanie lub haszowanie `tag_id`,
- dokumentować konsekwencje używania surowego numeru karty jako `tag_id`,
- nie mieszać zarządzania uprawnieniami Dahua z tagami HA; SmartPSS nadal pozostaje źródłem uprawnień kontroli dostępu.

### 22.5. Znaczenie dla projektu

Ta funkcja może być bardzo istotna, ponieważ pozwala używać kart kontroli dostępu Dahua jako pełnoprawnych identyfikatorów automatyzacji w Home Assistant.

Przykładowo:

```text
ta sama karta
   ↓
otwiera drzwi w Dahua
   ↓
jest rozpoznawana jako tag HA
   ↓
uruchamia automatyzacje lokalne
```

Dzięki temu `DBS Dahua Access` może łączyć system kontroli dostępu z natywnym modelem automatyzacji Home Assistant bez dodatkowych czytników RFID/NFC.

### 22.1. Pobieranie nazwy użytkownika po `user_id`

Lab potwierdza, że event kontroli dostępu zawiera `user_id`, np.:

```text
user_id=8
card=058D1C32
```

Po takim `user_id` można dociągać dane użytkownika z kontrolera przez NetSDK:

```text
OperateAccessUserService(... NET_EM_ACCESS_CTL_USER_SERVICE_GET ...)
```

Webowy lab robi teraz automatyczne wzbogacanie:

1. odbiera event z czytnika,
2. zapisuje `user_id` i numer karty,
3. jeśli nazwa użytkownika nie jest jeszcze w cache, odpytuje kontroler,
4. zapisuje wynik w `users_by_id`,
5. tabela zdarzeń i kafel drzwi pokazują nazwę z cache zamiast samego `ID`.

W docelowej integracji Home Assistant warto przenieść ten mechanizm jako cache użytkowników:

```yaml
event_type: dbs_dahua_access_event
event_data:
  result: access_granted
  user_id: "8"
  user_name: "..."
  card: "058D1C32"
  door: 2
  reader: 3
```

Ważne: nazwa użytkownika może pojawić się z krótkim opóźnieniem po pierwszym zdarzeniu, bo najpierw przychodzi event dostępu, a dopiero potem integracja odpytuje kontroler o szczegóły użytkownika.

## 23. Powiązanie zdarzeń kontroli dostępu z NVR / kamerami Dahua

Warto przewidzieć opcjonalną funkcję, w której zdarzenie z czytnika Dahua uruchamia akcję po stronie rejestratora lub kamery.

Przykładowy scenariusz:

```text
karta / PIN przy drzwiach
   ↓
ACCESS_EVENT z kontrolera
   ↓
integracja DBS Dahua Access
   ↓
wymuszenie alarmu / snapshotu / znacznika nagrania w NVR
   ↓
krótkie nagranie z wybranej kamery
```

### 23.1. Najbardziej obiecujący mechanizm

Rejestratory Dahua pozwalają mapować `Alarm Input` na akcje typu:

- nagrywanie wybranego kanału,
- snapshot z wybranego kanału,
- komunikat alarmowy,
- inne akcje alarmowe zależne od modelu NVR.

W NetSDK istnieje komenda:

```text
CtrlType.TRIGGER_ALARM_IN
```

opisana jako:

```text
Activate alarm input
```

To sugeruje, że można programowo zasymulować wejście alarmowe rejestratora. Jeżeli NVR ma skonfigurowane alarm input -> record channel, to event z czytnika może wywołać krótkie nagranie na kanale kamery bez potrzeby generowania fałszywego ruchu.

### 23.2. Wariant pewny sprzętowo

Najbardziej niezawodny wariant, niezależny od obsługi API konkretnego NVR:

```text
kontroler Dahua alarm out / relay
   ↓
wejście alarmowe NVR
   ↓
lokalna konfiguracja NVR: Alarm Input -> Record Channel
```

Zalety:

- działa jak natywna funkcja rejestratora,
- nie wymaga reverse engineeringu komendy SDK,
- można użyć pre-record i post-record ustawionych w NVR,
- zdarzenie będzie widoczne w NVR jako alarm.

Wady:

- wymaga okablowania,
- zużywa fizyczne wyjście/wejście alarmowe,
- trudniej mapować wiele drzwi na wiele kamer bez dodatkowej logiki.

### 23.3. Wariant programowy przez NetSDK

Do sprawdzenia na konkretnym rejestratorze:

```text
CLIENT_ControlDevice(
    login_id_nvr,
    CtrlType.TRIGGER_ALARM_IN,
    <parametr kanału/wejścia alarmowego>
)
```

Hipoteza testowa:

- logujemy się do NVR przez NetSDK,
- konfigurujemy w NVR lokalny alarm input oraz `Record Channel`,
- wysyłamy `TRIGGER_ALARM_IN` dla wejścia alarmowego,
- sprawdzamy, czy NVR wystawia alarm i czy pojawia się nagranie alarmowe na wybranym kanale.

Nie należy traktować tego jako potwierdzonej funkcji produkcyjnej, dopóki nie zostanie przetestowana na docelowym modelu rejestratora.

#### 23.3.1. Trop SDK: `NOTIFY_EVENT`

W `CtrlType` z paczki NetSDK jest też:

```text
CtrlType.NOTIFY_EVENT = 408
opis SDK: send event to device, corresponding to NET_NOTIFY_EVENT_DATA
```

To jest najbardziej obiecujący trop dla pytania: "czy można szturchnąć rejestrator na konkretnym kanale bez zmiany `RecordMode`".

Status w obecnej paczce Python:

- enum `NOTIFY_EVENT` istnieje,
- wrapper Python nie zawiera struktury `NET_NOTIFY_EVENT_DATA`,
- bez tej struktury nie da się jeszcze bezpiecznie zbudować poprawnego wywołania `CLIENT_ControlDeviceEx`,
- trzeba sprawdzić pełne nagłówki C/C++ SDK albo dokumentację rejestratora, a następnie dopisać strukturę w `ctypes`.

Wynik testu na kamerze `10.10.20.62`:

- login NetSDK OK,
- `StartListenEx` OK,
- testowane warianty przez `CLIENT_ControlDevice` i `CLIENT_ControlDeviceEx`,
- warianty parametru: prosty `c_int`, kilka układów pól `dwSize/channel/action/event`,
- nazwy zdarzeń: `MotionDetect`, `VideoMotion`, `AlarmLocal`, `Alarm`, `AccessControl`, `ManualSnap`,
- wynik: kamera zwraca `The current SDK does not support this function`.

Wniosek: `NOTIFY_EVENT` nie jest użyteczne na tej kamerze. Nadal może być użyteczne na NVR, bo to rejestrator jest docelowym miejscem szturchania osi czasu/kanału.

Hipoteza testowa dla NVR:

```text
CLIENT_ControlDeviceEx(
    login_id_nvr,
    CtrlType.NOTIFY_EVENT,
    NET_NOTIFY_EVENT_DATA(channel=<kanał NVR>, event=<typ zdarzenia>, action=start/pulse/stop),
    ...
)
```

Jeżeli konkretny NVR to obsłuży, będzie to lepsze niż przełączanie trybu nagrywania, bo powinno tworzyć zdarzenie po stronie urządzenia.

### 23.4. Preferencja: event zamiast zmiany konfiguracji

Preferowany model integracji to wywołanie zdarzenia po stronie NVR/kamery, a nie zmiana ustawień nagrywania.

Docelowo należy szukać mechanizmu typu:

```text
access_event z czytnika
   ↓
syntetyczny alarm / motion / local alarm / event na kanale wideo
   ↓
NVR/kamera wykonuje własną akcję nagrywania
```

Najbardziej pożądane warianty testowe:

1. Kamera jako pierwszy cel: lokalny alarm / external alarm / snapshot / event kamery.
2. `NOTIFY_EVENT` w NVR, jeśli pełny SDK/model pozwala wskazać kanał i typ zdarzenia.
3. `TRIGGER_ALARM_IN` w NVR, jeżeli można programowo wskazać wejście alarmowe powiązane z kanałem/kamerą.
4. Event kanałowy typu local alarm / remote alarm / motion, jeśli NetSDK lub HTTP API konkretnego modelu pozwala go zasymulować.
5. `CAPTURE_START` / `ManualSnap` jako eventowy snapshot.
6. `MARK_IMPORTANT_RECORD`, jeśli kanał jest nagrywany ciągle i chcemy tylko oznaczyć fragment.
7. Pobranie krótkiego klipu po zdarzeniu, jeśli NVR i tak nagrywa ciągle.

Niepożądany wariant produkcyjny:

```text
zmiana trybu nagrywania kanału na manual
```

Taki wariant może zostać tylko jako eksperyment laboratoryjny, bo modyfikuje konfigurację pracy NVR i może zostawić kanał w złym trybie po awarii integracji.

### 23.5. Konfiguracja docelowa w integracji

#### 23.5.1. Niezalecany fallback: bezpośrednia zmiana trybu nagrywania kanału

W NetSDK istnieje konfiguracja:

```text
EM_DEV_CFG_TYPE.RECORDMODE = "RecordMode"
NET_A_AV_CFG_RecordMode
```

Struktura `NET_A_AV_CFG_RecordMode` zawiera:

```text
nMode       0 = schedule, 1 = manual, 2 = off
nModeExtra1 0 = auto,     1 = manual, 2 = off
nModeExtra2 0 = auto,     1 = manual, 2 = off
```

To sugeruje, że NVR może pozwalać na bezpośrednie przełączenie konkretnego kanału w tryb ręcznego nagrywania, ale nie jest to preferowany mechanizm dla tej integracji.

Model działania:

```text
GetDevConfig(RECORDMODE, channel)
   ↓
zapamiętaj poprzedni tryb
   ↓
SetDevConfig(RECORDMODE, channel, manual)
   ↓
czekaj np. 15 s
   ↓
SetDevConfig(RECORDMODE, channel, poprzedni tryb)
```

To jest technicznie bezpośrednie, ale gorsze architektonicznie niż wywołanie eventu/alarmu:

- wymaga modyfikowania konfiguracji trybu nagrywania kanału,
- wymaga niezawodnego przywrócenia poprzedniego stanu,
- awaria integracji po ustawieniu `manual` może zostawić kanał w ręcznym nagrywaniu,
- może nie tworzyć w NVR normalnego zdarzenia alarmowego,
- może mieć inne zachowanie na różnych modelach NVR.

Dlatego preferowana kolejność testów powinna być:

1. `TRIGGER_ALARM_IN` + natywna konfiguracja NVR `Alarm Input -> Record Channel`.
2. Syntetyczny event kanałowy, np. local alarm / motion / remote alarm, jeśli model NVR/kamery to wspiera.
3. Snapshot / marker / pobranie klipu jako fallback.
4. `RECORDMODE` per kanał tylko jako awaryjny lab-hack, nie jako domyślna funkcja produkcyjna.

Konfiguracja nie powinna zakładać, że akcja wideo zawsze idzie bezpośrednio do NVR. Przy wielu czytnikach lepszy model to:

```text
czytnik / drzwi
   ↓
reguła mapowania
   ↓
cel wideo: kamera, kanał NVR, alarm input NVR lub kilka celów naraz
```

NVR pozostaje najlepszym celem, gdy zależy nam na zapisie w centralnym archiwum. Kamera może być lepszym pierwszym celem, gdy:

- kamera ma własne wejście alarmowe,
- kamera ma kartę SD / edge recording,
- potrzebny jest tylko snapshot,
- zdarzenie ma dotyczyć bardzo konkretnego ujęcia kamery,
- liczba czytników jest większa niż liczba dostępnych wejść alarmowych NVR.

#### 23.5.2. Kamera jako pierwszy cel zdarzenia

Ten wariant może być lepszy dla wielu czytników, bo mapowanie jest naturalne:

```text
czytnik przy drzwiach A
   ↓
kamera patrząca na drzwi A
   ↓
kamera generuje snapshot / alarm / event
   ↓
NVR zapisuje zdarzenie z tej kamery albo kamera zapisuje lokalnie
```

Zalety:

- mapowanie jest per fizyczne miejsce, nie per abstrakcyjny kanał NVR,
- łatwiej obsłużyć wiele czytników i wiele kamer,
- kamera może mieć własne wejścia alarmowe, kartę SD i snapshoty,
- NVR może pozostać tylko archiwum, jeżeli reaguje na eventy z kamer.

Ryzyka do sprawdzenia:

- wiele kamer pozwala odczytywać event motion/alarm, ale nie zawsze pozwala programowo taki event wstrzyknąć,
- syntetyczny motion może nie istnieć jako komenda; bardziej realny może być external/local alarm lub manual snapshot,
- jeśli nagranie ma trafić do NVR, trzeba sprawdzić, czy NVR zapisze event wygenerowany po stronie kamery,
- dla kamer bez SD/NVR pozostaje snapshot lub pobranie krótkiego strumienia przez integrację.

Priorytet testów dla kamery:

1. Login NetSDK/HTTP do kamery.
2. `ManualSnap` / `CAPTURE_START` dla konkretnej kamery.
3. Sprawdzenie, czy kamera ma alarm input i czy da się go programowo wyzwolić.
4. Sprawdzenie, czy wygenerowany event kamery pojawia się w NVR.
5. Dopiero potem próba syntetycznego motion/local alarm, jeśli model/API to wspiera.

Proponowana opcja per drzwi/czytnik:

```yaml
video_actions:
  - event: access_granted
    reader: 3
    door: 2
    target: nvr_alarm_input
    nvr_id: "nvr_piwnica"
    alarm_input: 1
    record_channels: [3]
    duration: 15
```

Wariant z bezpośrednim celem kamera:

```yaml
video_actions:
  - event: access_denied
    reader: 3
    door: 2
    target: camera
    camera_id: "kamera_piwnica_wejscie"
    action: snapshot
```

Wariant mieszany:

```yaml
video_actions:
  - event: access_denied
    reader: 3
    door: 2
    targets:
      - target: camera
        camera_id: "kamera_piwnica_wejscie"
        action: snapshot
      - target: nvr_alarm_input
        nvr_id: "nvr_piwnica"
        alarm_input: 1
        record_channels: [3]
```

Ważna zasada: integracja powinna mapować zdarzenie dostępu na abstrakcyjną akcję wideo, a nie zakładać jednego konkretnego typu urządzenia docelowego.

### 23.6. Zasady bezpieczeństwa

Funkcja powinna być domyślnie wyłączona.

Wymagania:

- osobne dane logowania do NVR, nie mieszać ich z kontrolerem dostępu,
- brak automatycznego kasowania lub modyfikacji konfiguracji NVR bez zgody użytkownika,
- czytelny log każdej wysłanej komendy,
- rate limit, żeby zapętlony czytnik nie generował setek alarmów,
- możliwość ograniczenia akcji tylko do `access_denied`, `door_forced`, `door_held_open` lub wybranych kart/użytkowników.

### 23.7. Plan testu w labie

Kolejny krok po podaniu danych NVR:

1. Dodać osobny plik `.env` dla NVR w `secrets/`.
2. Napisać małe narzędzie `dahua_nvr_alarm_lab.py`.
3. Sprawdzić login i podstawowe dane rejestratora.
4. Sprawdzić, czy NVR ma alarm inputs i kanały nagrywania.
5. Przetestować `TRIGGER_ALARM_IN` na kontrolowanym wejściu.
6. Zweryfikować w logach NVR, czy pojawił się alarm i nagranie.
7. Dopiero potem łączyć to z eventami z czytnika.

### 23.8. Wynik testu NVR 10.10.20.20

Rejestrator:

```text
host: 10.10.20.20
channels: 16
alarm_in: 0
alarm_out: 0
serial: 8B009B2PAZB62C5
type: 31
```

Ważna uwaga: Dahua NetSDK liczy kanały od zera. Dla kanału widocznego w UI jako `15` należy testować `channel=14`.

Kanał w UI rejestratora:

```text
D15 MAGAZYN
```

Lokalne `secrets/nvr_10_10_20_20.env` zostało ustawione na:

```text
DAHUA_NVR_CHANNEL=14
DAHUA_NVR_CHANNEL_UI=15
DAHUA_NVR_CHANNEL_ID=D15
DAHUA_NVR_CHANNEL_NAME=MAGAZYN
DAHUA_NVR_CHANNEL_LABEL=D15 MAGAZYN
```

Wyniki dla kanału SDK `14`:

- login przez NetSDK działa,
- `StartListenEx` działa,
- `Record` config działa,
- `RecordMode` config działa,
- `MotionDetect` config działa i pokazuje, że motion na tym kanale jest obecnie wyłączony (`Enable=false`),
- `Snap` config działa,
- `CAPTURE_START` zwraca błąd `Can not operate right now.`,
- `MARK_IMPORTANT_RECORD` zwraca `ok=True`,
- `TRIGGER_ALARM_IN input=1` zwraca `ok=True`,
- `NOTIFY_EVENT` przez testowane warianty `ControlDeviceEx` zwraca błąd `The current SDK does not support this function.`

Callbacki po aktywnym teście nie potwierdziły jeszcze jednoznacznie, że szturchnięcie dotyczy kanału 15. Rejestrator wysłał eventy motion dla innych kanałów:

```text
EVENT_MOTIONDETECT channel=2 action=1
EVENT_MOTIONDETECT channel=3 action=1
```

To najpewniej były realne zdarzenia ruchu na innych kamerach w trakcie testu, a nie skutek `TRIGGER_ALARM_IN` dla kanału 15.

Aktualny wniosek:

1. `NOTIFY_EVENT` nie jest praktyczną drogą na tym NVR w obecnym wrapperze NetSDK.
2. `MARK_IMPORTANT_RECORD` jest potwierdzonym, bezpiecznym fallbackiem, jeśli kanał jest już nagrywany.
3. `TRIGGER_ALARM_IN` jest przyjmowane przez NVR, ale trzeba jeszcze potwierdzić w UI rejestratora, czy tworzy wpis alarmowy / nagranie na osi czasu.
4. Nie ma jeszcze dowodu, że NVR pozwala szturchnąć syntetyczny `motion` bezpośrednio na wybranym kanale bez zmiany konfiguracji.

Kolejny test praktyczny:

1. Uruchomić `start_nvr_event_probe.bat`.
2. Sprawdzić w UI NVR oś czasu kanału 15 w czasie testu.
3. Sprawdzić, czy pojawia się marker ważnego nagrania po `MARK_IMPORTANT_RECORD`.
4. Sprawdzić, czy pojawia się alarm/event po `TRIGGER_ALARM_IN`.
5. Jeżeli nie ma zdarzenia na kanale 15, traktować `TRIGGER_ALARM_IN` tylko jako wejście globalne/alarmowe wymagające lokalnego mapowania w NVR.

### 23.9. Web lab: przyciski NVR dla D15 MAGAZYN

Webowy lab `start_kd1_web_lab.bat` ładuje teraz także `secrets/nvr_10_10_20_20.env` i pokazuje osobną sekcję NVR dla:

```text
D15 MAGAZYN
SDK channel: 14
UI channel: 15
```

Dostępne przyciski:

- `Oznacz ważne nagranie` -> `CtrlType.MARK_IMPORTANT_RECORD` z parametrem `channel=14`; wcześniej potwierdzone jako `ok=True`.
- `Trigger alarm input` -> `CtrlType.TRIGGER_ALARM_IN` z parametrem `alarm_input=1`; wcześniej potwierdzone jako `ok=True`, ale wymaga sprawdzenia na osi czasu NVR.
- `CAPTURE_START test` -> `CtrlType.CAPTURE_START` z parametrem `channel=14`; wcześniej zwracało `Can not operate right now`, zostaje jako szybki test porównawczy.

Panel pokazuje też callbacki z NVR, między innymi `MOTION_ALARM_EX`, `ALARM_ALARM_EX` i `EVENT_MOTIONDETECT`, jeśli rejestrator je odeśle po kliknięciu.

---

## 24. Start implementacji HACS

Po akceptacji planu rozpoczęto właściwy scaffold integracji Home Assistant / HACS.

Dodane elementy:

- `custom_components/dbs_dahua_access/` jako docelowy komponent HA,
- `hacs.json` i `manifest.json`,
- `Config Flow` dla dodawania kontrolera po IP, porcie, loginie i haśle,
- `reconfigure` dla zmiany IP / danych logowania tego samego kontrolera,
- `reauth` dla błędnych danych logowania,
- adapter `NetSDKAccessClient` dla logowania, nasłuchu, otwierania drzwi i pobierania użytkownika po `user_id`,
- modele zdarzeń, użytkowników, drzwi i informacji o kontrolerze,
- encje HA: `button`, `binary_sensor`, `sensor`,
- event HA `dbs_dahua_access_event`,
- dodatkowy event `tag_scanned` dla kart jako `tag_id=dahua:<numer_karty>`,
- testy normalizacji podstawowych zdarzeń.

Decyzje zaimplementowane w pierwszym scaffoldingowym przebiegu:

- jeden `Config Entry` = jeden kontroler,
- `Device` HA identyfikowane po numerze seryjnym kontrolera,
- nazwa kontrolera jest pobierana z konfiguracji Dahua, jeśli SDK ją zwróci; fallbackiem jest host,
- drzwi są wykrywane przez metody SDK, a dla starszego `ASC2204B-S` kanał jest uznawany tylko wtedy, gdy odpowiedź `AccessControl` ma `result=true` i zwraca dokładnie żądany numer kanału,
- nazwa drzwi z eventu SDK ma pierwszeństwo przed nazwą fallbackową,
- numer drzwi zostaje atrybutem technicznym,
- eventy z `user_id` są wzbogacane nazwą użytkownika przez `OperateAccessUserService`,
- PIN bez `user_id` pozostaje jako nieznany użytkownik, bez zgadywania.

Brama przed oznaczeniem jako produkcyjny HACS:

1. Potwierdzić działanie Dahua NetSDK na Home Assistant OS `arm64`.
2. Zdecydować sposób pakowania natywnych bibliotek NetSDK dla HACS.
3. Uruchomić test w prawdziwym HA na `10.10.30.30`.
4. Dodać testy HA z mockowanym klientem NetSDK.

### 24.1. Bundlowanie Dahua NetSDK w HACS

Pierwszy test w Home Assistant zwrócił `sdk_unavailable`, ponieważ HACS zainstalował komponent, ale nie miał skąd zainstalować prywatnego pakietu Dahua `NetSDK`.

Dodano bundlowane wheel'e z pobranych paczek SDK:

```text
NetSDK-2.0.0.1-py3-none-linux_x86_64.whl
NetSDK-2.0.0.1-py3-none-linux_i686.whl
NetSDK-2.0.0.1-py3-none-win_amd64.whl
```

Loader integracji wybiera wheel na podstawie systemu i architektury, rozpakowuje go lokalnie do ignorowanego katalogu runtime i dopiero wtedy importuje `NetSDK`.

Ważne ograniczenie: w aktualnie pobranych paczkach Dahua nie ma wheel'a Linux `arm64/aarch64`. Jeżeli Home Assistant działa na ARM64, integracja nadal nie może załadować natywnego SDK, ale pokaże już dokładny komunikat o braku tej architektury zamiast ogólnego `sdk_unavailable`.

### 24.2. Korekta wykrywania przejść

Wersja `0.1.1` ponownie popełniła błąd z fazy laboratoryjnego sondowania: `GetNewDevConfig("AccessControl", channel)` dla kanałów powyżej realnych przejść potrafi zwrócić odpowiedź, którą łatwo błędnie uznać za istnienie kolejnych drzwi.

W `0.1.2` wykrywanie zostało zmienione tak, aby najpierw pytać kontroler o liczbę przejść:

- główna metoda to `OperateAccessControlManager(... GETSUBCONTROLLER_INFO ...)`,
- jeżeli kontroler nie wspiera tej metody, integracja próbuje odczytać liczbę z `AccessControlGeneral`,
- kanały `AccessControl` są używane tylko do wzbogacenia nazw dla już ustalonej liczby drzwi,
- encje `Przejście 5+` zostają usunięte z rejestru encji, jeśli powstały po wcześniejszej wersji integracji,
- jeżeli kontroler nie odda liczby przejść żadną potwierdzoną metodą, integracja nie tworzy encji drzwi zamiast zgadywać ich liczbę.

### 24.3. Nazwy kontrolera i przejść z SDK

W `0.1.3` poprawiono priorytety zgodnie z labem:

- nazwa kontrolera jest pobierana z konfiguracji SDK `General.MachineName`, jeżeli kontroler ją zwraca,
- przy starcie integracja aktualizuje tytuł istniejącego `Config Entry`, jeśli wcześniej został zapisany sam host/IP,
- nazwy przejść są pobierane z `GETSUBCONTROLLER_INFO`, jeżeli metoda jest wspierana,
- `szDoorName` z eventu dostępu dalej ma pierwszeństwo jako najdokładniejsza nazwa przejścia dla danego zdarzenia,
- fallback `Przejście <nr>` jest używany dopiero, gdy SDK nie odda nazwy.

### 24.4. Rzeczywisty model urządzenia i walidacja kanałów

Kod `NET_DEVICEINFO_Ex.nDVRType=56` nie oznacza rejestratora. W enumie SDK jest to `NET_BSC_SERIAL`, opisane przez Dahua jako seria produktów kontroli dostępu. Nie wolno budować z tego wartości `Dahua DVR type 56`.

Potwierdzona metoda odczytu danych produktu:

```text
QueryDevState(EM_QUERY_DEV_STATE_TYPE.SOFTWARE)
NET_A_DEV_VERSION_INFO.szDevType
NET_A_DEV_VERSION_INFO.szDetailType
NET_A_DEV_VERSION_INFO.szSoftWareVersion
NET_A_DEV_VERSION_INFO.szHardwareVersion
```

Wynik na kontrolerze laboratoryjnym:

```text
model: DHI-ASC2204B-S
firmware: 2.000.0000000.8.R
hardware: RTL8201
SDK class: NET_BSC_SERIAL / kontroler dostępu
```

Ten sam kontroler potwierdził `AccessControl` dla kanałów `0..3` i odrzucił kanał `4`. Produkcyjna integracja liczy więc cztery drzwi na podstawie odpowiedzi urządzenia. Samo `ok=True` nie wystarcza: odpowiedź JSON musi dodatkowo zawierać `result=true` oraz `params.channel` równy kanałowi, o który zapytano. `AccessControlGeneral.ABLock.Doors` opisuje grupę blokady, więc nie może być używane jako liczba wszystkich drzwi.

Numery widoczne w Home Assistant są celowo przesunięte o jeden względem SDK: `Przejście 1` ma `sdk_channel=0`, a `Przejście 4` ma `sdk_channel=3`. `NET_CTRL_ACCESS_OPEN.nChannelID` jest według SDK numerowany od zera. Eventy `nDoor`, konfiguracja i komendy są normalizowane przez tę samą parę konwersji, aby nazwa, stan i przycisk zawsze dotyczyły tego samego fizycznego przejścia.

Kontroler laboratoryjny zwrócił trzy karty z przypisaniami do użytkowników `1`, `8` i `2`. Pola nazw osób i kart na tym egzemplarzu są puste, dlatego integracja używa dla nich etykiet `ID <user_id>` zamiast tworzyć nazwę. Gdy `NET_ACCESS_USER_INFO.szName` lub `szNameEx` jest wypełnione, ta nazwa trafia do natywnego rejestru tagów Home Assistant. Wspólny PIN metody `PWD_ONLY` nie jest tożsamością użytkownika; przypisanie osoby jest możliwe dla PIN-u osobistego lub `UserID+PIN`, jeżeli kontroler umieści `szUserID` w evencie.
