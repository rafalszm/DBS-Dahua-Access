# DBS Dahua Access

Integracja dla Home Assistant pozwalająca lokalnie obsługiwać kontrolery dostępu Dahua.

## Główne zalety

- **Konfiguracja przez UI** - dodajesz kontroler po IP, porcie, loginie i haśle.
- **Urządzenia i encje HA** - kontroler jako urządzenie, drzwi jako encje z przyciskami i sensorami.
- **Zdarzenia dostępu** - czytelne eventy z użytkownikiem, kartą, czytnikiem, drzwiami i wynikiem autoryzacji.
- **Tagi Home Assistant** - karty RFID mogą trafiać do `tag_scanned` jako `dahua:<numer_karty>`.
- **Lokalna komunikacja** - integracja działa po LAN przez Dahua NetSDK, bez chmury Dahua.
- **Bundlowane SDK** - integracja zawiera dostępne wheel'e Dahua NetSDK dla Linux x86_64/i686 oraz Windows amd64.

Po instalacji przejdź do **Ustawienia -> Urządzenia oraz usługi -> Dodaj integrację** i wyszukaj **DBS Dahua Access**.
