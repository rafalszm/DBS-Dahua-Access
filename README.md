# DBS Dahua Access (Home Assistant Integration)

Integracja Home Assistant / HACS dla lokalnych kontrolerów dostępu Dahua. Projekt rozwijany jest jako integracja firmy **Digital Best Solutions** dla kontrolerów Dahua działających w LAN, bez zależności od chmury Dahua.

![DBS Dahua Access](icon.png)

## Funkcje

* **Konfiguracja przez interfejs Home Assistant**: dodanie kontrolera przez IP, port, login i hasło.
* **Model urządzeń Home Assistant**: jeden fizyczny kontroler Dahua jest widoczny jako osobne urządzenie.
* **Encje drzwi i przejść**: przyciski otwarcia, status online, status drzwi i sensory ostatnich zdarzeń.
* **Nazwy z kontrolera**: integracja próbuje pobierać nazwę kontrolera oraz nazwy drzwi/przejść bezpośrednio z Dahua NetSDK.
* **Czytelne zdarzenia**: event `dbs_dahua_access_event` zawiera drzwi, czytnik, metodę, wynik, użytkownika, kartę i czas urządzenia.
* **Tagi Home Assistant**: odczyty kart emitują także `tag_scanned` jako `dahua:<numer_karty>`.
* **Obsługa wielu kontrolerów**: każdy kontroler jest osobnym wpisem konfiguracji.
* **Reconfigure i reauth**: zmiana IP/danych logowania oraz aktualizacja hasła bez usuwania integracji.

Kamery i NVR są poza zakresem pierwszej wersji integracji. Eksperymenty laboratoryjne pozostają w katalogu `tools/`.

## Status

Wersja `0.1.2` jest pierwszą wersją z bundlowanym Dahua NetSDK i bez nadmiarowego tworzenia przejść wykrytych przez fałszywie pozytywne sondowanie `AccessControl`.

W repozytorium są dołączone oficjalne wheel'e Dahua NetSDK dla:

* Linux x86_64,
* Linux i686,
* Windows amd64.

W pobranej paczce Dahua nie ma wheel'a Linux `arm64/aarch64`. Na Home Assistant OS uruchomionym na ARM64 integracja pokaże precyzyjny błąd braku NetSDK dla tej architektury, dopóki nie znajdziemy właściwej paczki Dahua ARM64.

## Instalacja przez HACS

1. W Home Assistant przejdź do **HACS** -> **Integracje**.
2. Kliknij menu w prawym górnym rogu i wybierz **Niestandardowe repozytoria**.
3. Wklej adres repozytorium:

   ```text
   https://github.com/rafalszm/DBS-Dahua-Access
   ```

4. Jako kategorię wybierz **Integracja**.
5. Zainstaluj **DBS Dahua Access** i zrestartuj Home Assistant.
6. Wejdź w **Ustawienia -> Urządzenia oraz usługi -> Dodaj integrację** i wyszukaj **DBS Dahua Access**.

## Konfiguracja

W formularzu integracji podajesz:

* adres IP lub hostname kontrolera,
* port Dahua NetSDK, domyślnie `37777`,
* login,
* hasło.

Integracja po połączeniu próbuje pobrać:

* numer seryjny kontrolera,
* nazwę urządzenia,
* model / typ urządzenia,
* dostępne drzwi lub przejścia,
* nazwy drzwi z konfiguracji albo z eventów kontrolera.

Integracja pyta kontroler o liczbę przejść przez `GETSUBCONTROLLER_INFO`, a jeśli urządzenie tego nie obsługuje, próbuje odczytać liczbę z `AccessControlGeneral`. Lab pokazał, że samo sondowanie kolejnych kanałów `AccessControl` może zwracać fałszywe pozytywy, dlatego nie tworzymy już encji `Przejście 5+` tylko dlatego, że SDK przyjęło zapytanie konfiguracyjne.

## Eventy Home Assistant

Główny event:

```yaml
event_type: dbs_dahua_access_event
data:
  controller: KD1 PIWNICA KOZLA
  controller_serial: AF0D0F9PAJ827B8
  controller_ip: 10.10.20.32
  door: 2
  door_label: Magazyn
  reader: "3"
  event: access_granted
  method: card
  user_id: "8"
  user_name: Jan Kowalski
  card_tag_id: dahua:058D1C32
  device_time: "2026-09-19T19:12:51"
```

Dla kart emitowany jest również natywny event Home Assistant:

```yaml
event_type: tag_scanned
data:
  tag_id: dahua:058D1C32
```

## Rozwój lokalny

Pliki lokalne i sekrety są ignorowane przez git:

* `secrets/`
* `sdk/`
* `.venv/`
* `lab_captures/`

Bundlowane wheel'e NetSDK są trzymane w:

```text
custom_components/dbs_dahua_access/vendor/wheels/
```

Testy podstawowej normalizacji:

```powershell
.venv\Scripts\python.exe -m py_compile custom_components\dbs_dahua_access\*.py custom_components\dbs_dahua_access\dahua\*.py tests\test_normalizer.py
```

## Autor i licencja

Projekt jest rozwijany przez firmę **Digital Best Solutions** ([dbsservice.pl](https://dbsservice.pl/)).

Oprogramowanie jest udostępniane na warunkach określonych w pliku [LICENSE](LICENSE). Korzystanie z niego jest bezpłatne do celów prywatnych i niekomercyjnych. Komercyjne wdrożenie lub użycie w ramach prowadzonej działalności gospodarczej wymaga uzyskania pisemnej zgody oraz zakupienia licencji komercyjnej.
