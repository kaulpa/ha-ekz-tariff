# EKZ API Issues

## 1. `electricity_standard` liefert Business-Tarif statt Privatkunden-Tarif

- **Entdeckt:** 2026-03-25
- **API-Call:** `GET /v1/tariffs?tariff_name=electricity_standard`
- **Erwartet:** Privatkunden-Tarif "EKZ Energie Erneuerbar" (14.38 Rp. Winter / 9.73 Rp. Sommer)
- **Geliefert:** 13.3 Rp. (flat, ganzjährig) — entspricht dem Business-Tarif
- **Update 2026-03-28:** 13.3 Rp × 1.081 (MWST 8.1%) = 14.38 Rp — der Wert ist korrekt, die API liefert **netto** (ohne MWST). Die vermeintliche Differenz war die fehlende MWST.
- **Status:** GELÖST — Preise sind netto, MWST muss separat aufgeschlagen werden

## 2. `customerTariffs` publication_timestamp ist Request-Zeitpunkt, nicht Publikationszeit

- **Entdeckt:** 2026-03-25
- **API-Call:** `GET /v1/customerTariffs?tariffType=electricity_dynamic&ems_instance_id=...&start_timestamp=...&end_timestamp=...`
- **Erwartet:** Zeitpunkt wann EKZ die Tarife publiziert hat (z.B. 17:15 für Tomorrow-Preise)
- **Geliefert:** Zeitpunkt des API-Requests (z.B. 21:47 bei Fetch um 21:47)
- **Update 2026-03-28:** Am 27.3. zeigte publication_timestamp 17:15:00 UTC (18:15 lokal), Fetch war 17:15:01. Möglicherweise hat EKZ dies korrigiert oder es funktioniert seit Aktivierung der dynamischen Tarif-Vorbereitung.
- **EKZ bestätigt:** Publication timestamp wird täglich korrekt geliefert (Telefonat 27.3.)
- **Status:** VERMUTLICH GELÖST — nach 1.4. verifizieren

## 3. `customerTariffs` liefert ebenfalls Geschäftskunden-Tarif

- **Entdeckt:** 2026-03-26
- **API-Call:** `GET /v1/customerTariffs?tariffType=electricity_dynamic&ems_instance_id=...`
- **Erwartet:** Privatkunden All-in 25.93 Rp/kWh (Q1 2026)
- **Geliefert:** 23.83 Rp/kWh integrated (= electricity 13.30 + grid 10.53). regional_fees 0.16 Rp nicht im integrated enthalten. metering fehlt ganz.
- **Update 2026-03-28:** Alle Preise sind **netto**. (13.30 + 10.53 + 0.16) × 1.081 = 25.93 Rp — exakt der konfigurierte Q1-Wert. Die API liefert korrekte Privatkunden-Preise in netto.
- **Baseline-Berechnung:** electricity (Public, 0.1330) + grid (customerTariffs, 0.1053) + regional_fees (customerTariffs, 0.0016) = 0.2399 CHF/kWh netto = 25.93 Rp brutto
- **Status:** GELÖST — Preise sind netto, kein Business-Tarif-Problem

## 4. Dynamische Preise vor offiziellem Start erhalten

- **Entdeckt:** 2026-03-26
- **Beobachtung:** Über `customerTariffs` mit `tariffType=electricity_dynamic` wurden bereits mehrere Wochen lang bis zum 25.3.2026 Preisdaten geliefert, obwohl der dynamische Tarif offiziell erst ab 1.4.2026 gilt.
- **Verhalten:** Alle Slots haben identischen Preis (23.83 Rp netto) — de facto ein Flat-Tarif im Dynamic-Format
- **Update 2026-03-28:** API liefert auch Morgen-Daten jederzeit (nicht nur nach 18:15). Dies liegt am Pre-Dynamic-Modus. Ab 1.4. werden Morgen-Daten vermutlich erst nach Publikationszeit (~18:00) verfügbar sein.
- **Status:** ERWARTET — Pre-Dynamic Flat-Rate bis 31.3.

## 5. Public API Grid-Preis weicht ab

- **Entdeckt:** 2026-03-28
- **Public API (ohne tariff_name):** grid = 0.1098 CHF/kWh (netto)
- **customerTariffs:** grid = 0.1053 CHF/kWh (netto)
- **Differenz:** 0.45 Rp — Public API hat höheren Grid-Preis
- **Auswirkung:** Baseline wird korrekt aus Public electricity + customerTariffs grid/regional_fees berechnet (nicht aus Public grid)
- **Status:** BEKANNT — Public API Grid wird nicht für Baseline verwendet

## 6. DST-Handling: 93 statt 92 Slots

- **Entdeckt:** 2026-03-28
- **Public API** für 29.3.2026 (DST-Tag): liefert 93 Slots statt erwarteter 92
- **Letzter Slot:** `start_timestamp: 2026-03-30T00:00:00+02:00` — gehört zum nächsten Tag
- **customerTariffs** für 29.3.2026: liefert 97 Slots (92 für 29.3. + 5 Überlauf für 30.3.)
- **Auswirkung:** Validator filtert korrekt nach Zieldatum, Überlauf-Slots werden ignoriert
- **Status:** KEIN PROBLEM — Validator handhabt dies korrekt
