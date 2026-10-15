# Now Playing voor de platenspeler

Een microfoon naast de platenspeler luistert mee. Een computer (laptop of Raspberry Pi) herkent het nummer via Shazam en toont de albumhoes met "Now playing" op een webpagina. Een oude iPad of ander scherm opent die pagina in de browser.

## Wat je nodig hebt

- Een computer die aan blijft staan: Raspberry Pi 3/4/5 of een laptop, met Python 3.10+
- Een microfoon (USB-microfoon bij de Pi). Of beter: de line-out van je versterker via een USB-geluidskaart
- Een iPad of scherm in hetzelfde wifi-netwerk

## Installeren

```bash
# Raspberry Pi / Linux: eerst de audiobibliotheek
sudo apt install libportaudio2

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python server.py
```

Zoek het IP-adres van de computer op (`hostname -I`) en open `http://<ip>:8080` op de iPad.

## Het scherm

Veeg naar links of rechts om te wisselen tussen drie pagina's:

1. **Hoes**: albumhoes, titel, artiest en een voortgangsbalk. Shazam vertelt hoe ver het nummer is. De lengte komt van iTunes.
2. **Songtekst**: meelopende tekst zoals in Apple Music, via lrclib.net. Is er geen tijdgecodeerde tekst, dan zie je de gewone tekst.
3. **Info**: album, jaar, genre, label, tracknummer, duur en een korte beschrijving van de artiest van Wikipedia.

Als er niets speelt, toont het scherm een klok.

Wil je het ontwerp zien zonder microfoon? Start dan `DEMO=1 python server.py`.

## Op de iPad

1. Open de pagina in Safari.
2. Deel-knop, "Zet op beginscherm". Dan opent hij schermvullend.
3. Instellingen, Scherm en helderheid, Automatisch vergrendelen: Nooit.
4. Optioneel: Instellingen, Toegankelijkheid, Begeleide toegang, zodat de app vast blijft staan.

## Instellingen

Via omgevingsvariabelen, bijvoorbeeld `INTERVAL=30 python server.py`:

| Variabele | Standaard | Betekenis |
|---|---|---|
| `RECORD_SECONDS` | 8 | Lengte van elke opname |
| `INTERVAL` | 20 | Seconden tussen herkenningen |
| `SILENCE_RMS` | 0.005 | Onder dit volume telt het als stilte |
| `IDLE_AFTER` | 90 | Na zoveel seconden zonder herkenning toont het scherm "Zet een plaat op" |
| `AUDIO_DEVICE` | standaard | Naam of nummer van de microfoon. Lijst: `python -m sounddevice` |
| `PORT` | 8080 | Poort van de webpagina |

## Automatisch starten op een Raspberry Pi

Maak `/etc/systemd/system/nowplaying.service`:

```ini
[Unit]
Description=Now Playing
After=network-online.target sound.target

[Service]
WorkingDirectory=/home/pi/now-playing
ExecStart=/home/pi/now-playing/venv/bin/python server.py
Restart=always
User=pi

[Install]
WantedBy=multi-user.target
```

Daarna `sudo systemctl enable --now nowplaying`.

## Beperkingen

- Shazam herkent geen zeldzame persingen, live-opnames of nummers die niet in de database staan.
- `shazamio` gebruikt een onofficiële Shazam-API. Als die ooit stopt, is AudD of ACRCloud een alternatief (betaald, met API-sleutel).
- Een microfoon vangt ook praten op. Een kabel vanaf de versterker geeft betere herkenning.
