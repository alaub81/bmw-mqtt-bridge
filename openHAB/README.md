# BMW-Fahrzeugwidgets für openHAB MainUI

Die Datei `bmw-vehicle-widgets.yml` enthält beide wiederverwendbaren
**Komfort**-Widgets: `bmw_vehicle_comfort_list` für Listen und
`bmw_vehicle_comfort_card` für eigene Fahrzeugkarten. Es gibt keine Beispielseite
und keinen Demo-Modus mehr.

## Installation

Die Datei nach `$OPENHAB_CONF/yaml/bmw-vehicle-widgets.yml` kopieren und
MainUI neu laden. Sie enthält `version: 1` und beide Definitionen unter `widgets:`.
Bei einer Aktualisierung wird die bisherige dateibasierte Seite
**BMW Widget-Test** aus dieser Konfiguration entfernt.

Die Widgets werden einer Vehicle-Equipment-Gruppe unter **Metadaten hinzufügen**
als **Default List Widget** bzw. **Default Standalone Widget** zugeordnet.
In beiden Zuordnungen Fahrzeugname, Antrieb und die gewünschten Items auswählen.
Auf einer in MainUI angelegten Layout-Seite können ebenfalls Widgetinstanzen
hinzugefügt und dort konfiguriert werden. Die Widgetdefinitionen selbst sind bei
Dateiinstallation schreibgeschützt; ihre Instanzen bleiben konfigurierbar.

`vehicleGroup` dokumentiert die Fahrzeugzuordnung. Die Messwerte werden über
explizite Itemparameter gewählt, nicht automatisch aus der Gruppe ermittelt.
Die Zuordnung ersetzt nicht jede automatisch erzeugte Gruppenüberschrift und
blendet nicht automatisch einzelne Point-Items des semantischen Modells aus.

Alle Widgetdefinitionen werden ausschließlich in dieser gemeinsamen Datei
gepflegt. Bei der Umstellung eine vorhandene `vehicle-comfort-file-config.yml`
auf dem openHAB-Server durch `bmw-vehicle-widgets.yml` ersetzen; nicht beide
Dateien gleichzeitig behalten. Die Widget-UIDs bleiben unverändert, bestehende
Zuordnungen und Instanzen können weiterverwendet werden.

## Sichtbarkeit

Ein Messwert oder Status erscheint nur, wenn sein Itemparameter gesetzt ist.
Das gilt für Reichweite, Akku, Tank, Verriegelung, Ladestatus, Ladeziel,
Restladezeit, Ladeleistung, Reifenwarnung, Öffnungen, Kilometerstand und Empfangszeit.
Auch jeder einzelne Reifendruck wird unabhängig ausgeblendet. Ohne ein
Reifendruck-Item wird die Reifendrucksektion ausgeblendet. Die Detailansicht lässt
sich auch ohne Reifendruck aufklappen, wenn Fenster- oder Tür-Items ausgewählt sind.

Ist ein Item hinterlegt, liefert aber keinen gültigen Wert, erscheint `—` bzw.
ein unbekannter Status. Gültige Nullwerte bleiben sichtbar.
Akku und Ladeinformationen sind für Elektro/Hybrid vorgesehen; Tank für
Diesel/Benzin/Hybrid. Fahrzeugname und Antriebsbezeichnung bleiben sichtbar.

Akku- und Tankbalken werden nach ihrem Prozentwert eingefärbt: unter 10 % rot,
10 bis unter 30 % gelb, 30 bis einschließlich 70 % orange, über 70 % grün.
Der Prozentwert bleibt zusätzlich als Text sichtbar. Ungültige Prozentwerte
oder Werte außerhalb von 0–100 erzeugen keinen Balken.

## Ladeleistung der Wallbox

Unter **Energie und Kilometer → Ladeleistung Wallbox (W / kW)** das Leistungs-Item
vom Strommesser auswählen (`chargingPowerItem`). Das Widget zeigt beispielsweise
**Ladeleistung: 7.200 W**. `W`, `kW` und Zahlen ohne Einheit werden unterstützt;
`kW` wird in Watt umgerechnet, ein Wert ohne Einheit wird als Watt interpretiert.
Einheiten wie `Wh`, `kWh` oder `A` sind keine Leistung und werden nicht als Watt
interpretiert. Eine Wh-/kWh-Verbrauchsmessung ist dafür nicht geeignet.

Die Ladeleistung bleibt bei hinterlegtem Item sichtbar, auch bei `0 W`.
Die **Standby-Grenze Wallbox (W)** (`chargingPowerStandbyWatts`) beträgt
standardmäßig **10 W**. Messwerte bis einschließlich dieser Grenze erscheinen
als `0 W`, damit beispielsweise 3–5 W Eigenverbrauch nicht als Ladeleistung
angezeigt werden. Größere Messwerte bleiben unverändert; es wird kein
Standby-Verbrauch abgezogen. Die Grenze ist in jeder Widgetinstanz einstellbar.
Sie benötigt kein `chargingItem` und leitet aus dem Wallboxverbrauch keinen
Fahrzeug-Ladestatus ab. Wenn `chargingItem` vorhanden ist, wird dessen Status
zusätzlich angezeigt. Eine Wallbox kann auch ohne ladendes Auto Strom verbrauchen.

## Werte und Zustände

### Ladekabel-Symbol

Unter **Status und Empfangszeit → Ladekabel angeschlossen** das Anschlussstatus-Item
auswählen (`chargingCableItem`). Bei Elektro/Hybrid steht ein kleines Stecker-Symbol
links neben dem Ladestatus und der Ladeleistung. Auf schmalen Ansichten bricht die
Zeile um. Die Anzeige besteht ausschließlich aus dem Symbol:

- Grüner Stecker: angeschlossen.
- Dezenter, durchgestrichener Stecker: getrennt.
- Oranges Fragezeichen: unbekannt.

Der vollständige Zustand bleibt als Tooltip und für Screenreader verfügbar.

Ohne hinterlegtes Item wird das Symbol ausgeblendet. Es funktioniert auch, wenn
nur der Kabelstatus und kein Ladestatus-/Leistungs-Item konfiguriert ist.
Ein angeschlossenes Kabel bedeutet nicht automatisch, dass das Fahrzeug lädt;
die Ladestatuszeile und die Standby-Grenze der Leistung bleiben unabhängig davon.

Standardmäßig gelten `ON`, `TRUE`, `CONNECTED`, `PLUGGED_IN` als angeschlossen und
`OFF`, `FALSE`, `DISCONNECTED`, `UNPLUGGED` als getrennt. Die Mehrfachfelder
**Ladekabel: angeschlossen** (`chargingCableConnectedStates`) und
**Ladekabel: getrennt** (`chargingCableDisconnectedStates`) passen die Zuordnung
an die tatsächlichen Rohzustände an. Groß-/Kleinschreibung und umgebende Leerzeichen
werden ignoriert. `NULL`, `UNDEF`, fehlende oder nicht zugeordnete Werte bleiben
unbekannt. Bei doppelt zugeordneten Zuständen hat angeschlossen Vorrang.

| Parameter | Erwarteter Item-Zustand |
| --- | --- |
| `driveType` | `EV` (Elektro), `DIESEL`, `ICE` (Benzin), `PHEV` (Plug-in-Hybrid) |
| `batteryItem`, `fuelItem`, `chargeTargetItem` | Prozent, z. B. `72` oder `72 %` |
| `rangeItem`, `electricRangeItem`, `mileageItem` | Kilometer, z. B. `312` oder `312 km` |
| `chargeRemainingItem` | Minuten, z. B. `24` oder `24 min` |
| `chargingPowerItem` | Watt oder Kilowatt, z. B. `7200 W` oder `7.2 kW` |
| `lockItem` | Standard: `ON` verriegelt, `OFF` entriegelt; konfigurierbar |
| `chargingItem` | Standard: `ON` lädt, `OFF` lädt nicht; mehrere Zustände konfigurierbar |
| `windowItems`, `doorItems` | Mehrfachauswahl der einzelnen Fenster bzw. Türen und des Kofferraums; Switch, Contact oder String |
| `openingsItem` (Fallback) | `ON`: mindestens eine überwachte Öffnung offen; `OFF`: alle überwachten Öffnungen zu |
| `tireWarningItem` | `ON`: Warnung gemeldet; `OFF`: keine Warnung gemeldet |
| Vier Reifendruck-Items | `Number:Pressure`, z. B. `290 kPa` oder `2.9 bar`; Anzeige in bar |
| `lastUpdateItem` | DateTime, verknüpft mit Homie `telemetry#last-update` |

Bei Verriegelung und Ladestatus sind die exakten Rohzustände konfigurierbar,
z. B. `lockedState: LOCKED` und `unlockedState: UNLOCKED` bei String-Items.
Die Widgets senden keine Befehle an das Fahrzeug.

Für Reifendruck werden bar, kPa, Pa, hPa, MPa und psi in bar umgerechnet und
mit zwei Dezimalstellen angezeigt: `290 kPa` wird zu `2,90 bar`.
Die Rohzustandseinheit hat Vorrang vor `items.unit`. Bei einheitenlosen
Number-Items die **Reifendruck-Einheit bei Werten ohne Einheit** einstellen
(Standard: bar; für einheitenlose BMW-kPa-Werte: kPa).
Empfohlen ist `Number:Pressure` mit `unit: kPa`; eine State Description
`%.2f bar` kann dann die normale openHAB-Anzeige in bar formatieren. Das Widget
rechnet den Rohwert selbst um und verwendet nicht den formatierten Anzeigetext.

Außer bei Ladeleistung und Reifendruck erfolgt keine automatische Einheitenumrechnung.
Zahlen-Items in den angegebenen Einheiten bereitstellen. Rohzustände verwenden
einen Dezimalpunkt; die Ausgabe wird deutsch formatiert.
Aus Druckwerten wird kein Reifen-OK abgeleitet. Dafür ein tatsächliches
Warnungs-Item auswählen. Ein einzelnes Tür-Item darf nicht als Gesamtstatus aller
Türen und Fenster verwendet werden; die neuen Mehrfachauswahlen werten alle
ausgewählten Einzel-Items gemeinsam aus.

**Letzter Empfang** ist die Bridge-Empfangszeit der letzten gültigen BMW-Nachricht.
Bei Teilnachrichten können einzelne Werte älter sein. Die Verriegelung wird als
zuletzt gemeldeter Zustand beschriftet; die Empfangszeit wird absolut angezeigt.

## Fenster, Türen und Kofferraum

Unter **Fenster, Türen und Kofferraum** zwei getrennte Mehrfachauswahlen befüllen:

- **Fenster (mehrere Items)** (`windowItems`): einzelne Fenster auswählen.
- **Türen und Kofferraum (mehrere Items)** (`doorItems`): einzelne Türkontakte und
  den Kofferraum auswählen. Eine zusätzliche Item-Gruppe ist nicht erforderlich.

Die eingeklappte Anzeige fasst alle ausgewählten Items zusammen, z. B.
**Öffnungen: alle überwachten zu** oder **⚠ Öffnungen: 2 offen**.
Bei fehlenden oder unbekannten Werten erscheint z. B. **1 unbekannt** bzw.
**2 offen · 1 unbekannt**. Ein unbekanntes Item wird niemals als geschlossen
gezählt. Nur die ausgewählten Öffnungen werden überwacht; leere Kategorien werden
nicht angezeigt. Doppelte Item-Namen zählen im Gesamtstatus nur einmal.

**Öffnungen · antippen** öffnet die Detailansicht mit getrennten Abschnitten
**Fenster** und **Türen & Kofferraum**. Jeder Eintrag zeigt das in openHAB
hinterlegte Item-Label (ohne Label den lesbar formatierten Item-Namen) sowie **Offen**, **Geschlossen** oder **Unbekannt**. Bei unbekannten
Werten wird zusätzlich der Rohzustand angezeigt. Sind auch Reifendruck-Items
konfiguriert, erscheinen deren Details in derselben aufklappbaren Ansicht.

Fenster und Türen/Kofferraum haben getrennte Zustandszuordnungen:

| Kategorie | Offen | Geschlossen |
| --- | --- | --- |
| Fenster | `OPEN`, `OPENED`, `INTERMEDIATE`, `TILTED`, `PARTIALLY_OPEN` | `CLOSE`, `CLOSED` |
| Türen und Kofferraum | `TRUE`, `ON` | `FALSE`, `OFF` |

Die vier Mehrfachfelder **Fenster: offen / teilweise offen** (`windowOpenStates`),
**Fenster: geschlossen** (`windowClosedStates`), **Türen und Kofferraum: offen**
(`doorOpenStates`) und **Türen und Kofferraum: geschlossen** (`doorClosedStates`)
passen die Listen unabhängig voneinander an. Ohne eigene Liste gelten die Werte
in der Tabelle. Groß-/Kleinschreibung und umgebende Leerzeichen werden ignoriert.
Für BMW-Fenster können z. B. `OPEN` und `INTERMEDIATE` als zwei einzelne Einträge
hinterlegt werden; im YAML lautet die Konfiguration `windowOpenStates: [OPEN, INTERMEDIATE]`.
Teilweise offene oder gekippte Fenster zählen als offen. Wenn ein Zustand in
beiden Listen seiner Kategorie steht, hat offen Vorrang. `NULL`, `UNDEF`, fehlende
Items und nicht zugeordnete Werte bleiben unbekannt. Wird dasselbe Item in beiden
Kategorien ausgewählt, gilt seine Fensterzuordnung auch für den Gesamtstatus.

BMW-Öffnungs-Channels wie `cabinDoorRow1DriverIsOpen`, `bodyTrunkDoorIsOpen`
und `cabinWindowRow1DriverStatus` eignen sich für diese Auswahlen.
Den Verriegelungszustand `SECURED` weiterhin über **Verriegelungsstatus** anzeigen;
er sagt nicht, ob jede einzelne Öffnung geschlossen ist.

Das bisherige **Öffnungsstatus-Sammel-Item** (`openingsItem`) bleibt als Fallback
verwendbar, solange beide Mehrfachauswahlen leer sind. Sobald Einzel-Items
hinterlegt sind, bestimmen ausschließlich diese den Gesamtstatus.

## iPhone und Prüfung

In der openHAB-App die MainUI öffnen. Eine Karte pro Zeile verwenden.
Hauptwerte sind 23 px groß, weitere Werte 14–16 px, Zusatzinformationen 12–13 px.
Die Anzeige verwendet MainUI-Themefarben und unterstützt den Dunkelmodus.

YAML und Ausdruckslogik werden lokal geprüft. Die Öffnungsübersicht und ihre
Detailansicht wurden zusätzlich im MainUI-Widgeteditor geprüft. Die Darstellung
der eigenen Itemauswahl und die iPhone-Darstellung nach dem Import kontrollieren.

Referenzen: [YAML-Widgets](https://www.openhab.org/docs/configuration/yaml/widgets),
[Default Item Widgets](https://www.openhab.org/docs/tutorial/item_widgets),
[Widgetausdrücke](https://www.openhab.org/docs/ui/widget-expressions-variables.html).

## Kompakte Anzeige und fehlende Energiemenge

Die sichtbaren Beschriftungen **Akku** und **Tank** sind durch neutrale
Material-Symbole (`battery_full` und `local_gas_station`) mit 22 px Größe ersetzt,
passend zum Ladestecker. Tooltip und Screenreader benennen die Symbole weiterhin.
Nachlade-/Nachtankmenge steht neben dem Symbol, aktueller Inhalt und Prozentwert
bleiben rechts. Ohne Berechnungsquelle steht links nur das Symbol. Die Symbole
folgen der bisherigen Sichtbarkeit der Akku-/Tanksektion.

Der Kilometerstand steht hinter dem Antrieb unter dem Fahrzeugnamen. Die letzte
Empfangszeit erscheint unter dem Verriegelungsstatus; die Ladeleistung steht in
der Ladestatuszeile.

**Akku · 8,4 kWh nachladen** verwendet bevorzugt das optionale Item
`batteryMissingEnergyItem` (BMW `smeEnergyDeltaFullyCharged`). Wh werden in kWh
umgerechnet. Alternativ die nutzbare Gesamtkapazität über `batteryCapacityItem`
als kWh-/Wh-Item oder über `batteryCapacityKwh` als festen kWh-Wert konfigurieren.
Die Schätzung ist Kapazität × (100 − Akkustand) / 100, ohne Ladeverluste.
Beispiel: 40 kWh Gesamtkapazität und 75 % Ladezustand ergeben 10 kWh nachzuladen.
Die Einheit ist **kWh**, nicht kW/h.

Reihenfolge: direktes Delta-Item → Kapazitäts-Item plus Ladeprozent → feste
Kapazität plus Ladeprozent. Wenn eine ausgewählte Quelle keinen gültigen Wert
liefert, erscheint `—`; es wird nicht unbemerkt zu einer anderen Quelle gewechselt.
Ein reiner Prozentwert reicht ohne Kapazität nicht für eine kWh-Berechnung.

Der Tankinhalt aus `fuelRemainingItem` steht rechts vor dem Prozentwert,
z. B. **42,2 l · 64 %**, auch ohne Tankkapazität. Links erscheint zusätzlich
**Tank · 23,8 l nachtanken**, wenn `tankCapacityLiters` konfiguriert ist. Mit dem optionalen
Item `fuelRemainingItem` (BMW `remainingFuel`, in Litern) wird dessen Inhalt von
der Kapazität abgezogen. Andernfalls wird aus Kapazität und Tank-Prozentstand
geschätzt. Kapazitäten werden pro Fahrzeug eingestellt; es gibt keine Vorgaben.
Fehlende oder ungültige konfigurierte Messwerte zeigen **—**. Ohne Konfiguration
bleiben links nur die Akku- bzw. Tanksymbole.

Rechts neben dem Akkubalken steht der **aktuelle Energieinhalt vor den Prozenten**:
Kapazität × Ladeprozent / 100. Die Gesamtkapazität stammt aus `batteryCapacityItem`
oder alternativ dem festen Wert `batteryCapacityKwh`. Bei 38,76 kWh und 93 %
erscheint **36,0 kWh · 93 %**. Diese berechnete Energiemenge ist eine Schätzung.
Das Delta-Item beeinflusst weiterhin nur die links angezeigte nachzuladende Energie.

`batteryEnergyItem` ist ausschließlich für ein optionales Item mit **aktuell
gespeicherter Energie** vorgesehen und hat als direkter Messwert Vorrang vor
der Berechnung. Kein Gesamtkapazitäts-Item hier eintragen. Für die Berechnung
dieses Feld leer lassen und **Nutzbare Akkukapazität als Item** oder den festen
Kapazitätswert einstellen. Wh werden in kWh umgerechnet. Ohne direkte Energie
oder konfigurierte Gesamtkapazität erscheinen weiterhin nur die Prozentwerte.

## Mehrere Ladezustände

Unter **Status** können in **Zustände: lädt** (`chargingState`) mehrere
Rohzustände einzeln hinterlegt werden, z. B. `CHARGING` und `CHARGINGAC`.
Für **Zustände: lädt nicht** (`notChargingState`) ist ebenfalls eine
Mehrfachauswahl möglich. Groß-/Kleinschreibung muss zum Item passen.

```yaml
chargingState: [CHARGING, CHARGINGAC]
notChargingState: [NOCHARGING, FINISHED]
```

Die Standardzuordnung bleibt `ON` / `OFF`. Bestehende Konfigurationen mit
einem einzelnen Textwert funktionieren weiterhin.
