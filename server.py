"""Now Playing: luistert via een microfoon naar de platenspeler,
herkent het nummer met Shazam en toont hoes, songtekst en info op een webpagina."""
import asyncio
import io
import os
import re
import time
import wave
from urllib.parse import quote

import aiohttp
import numpy as np
from aiohttp import web

SAMPLE_RATE = 44100
RECORD_SECONDS = int(os.environ.get("RECORD_SECONDS", 8))
INTERVAL = int(os.environ.get("INTERVAL", 20))
SILENCE_RMS = float(os.environ.get("SILENCE_RMS", 0.005))
IDLE_AFTER = int(os.environ.get("IDLE_AFTER", 90))
PORT = int(os.environ.get("PORT", 8080))
DEVICE = os.environ.get("AUDIO_DEVICE")  # naam of index, leeg = standaard
DEMO = os.environ.get("DEMO") == "1"
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=10)
UA = {"User-Agent": "now-playing-turntable/1.0"}

EMPTY_TRACK = {"title": "", "artist": "", "album": "", "cover": "", "duration": 0,
               "year": "", "genre": "", "label": "", "track_number": 0, "track_count": 0,
               "lyrics": [], "plain_lyrics": "", "bio": "", "bio_source": ""}
state = {"playing": False, "offset": 0.0, "matched_at": 0.0, "updated": 0.0, **EMPTY_TRACK}
last_match = 0.0


# ---------- audio ----------

def record() -> np.ndarray:
    import sounddevice as sd
    device = int(DEVICE) if DEVICE and DEVICE.isdigit() else DEVICE
    audio = sd.rec(RECORD_SECONDS * SAMPLE_RATE, samplerate=SAMPLE_RATE,
                   channels=1, dtype="float32", device=device)
    sd.wait()
    return audio[:, 0]


def to_wav(audio: np.ndarray) -> bytes:
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


# ---------- herkenning ----------

def parse_shazam(result: dict) -> dict | None:
    track = result.get("track")
    if not track:
        return None
    meta = {}
    plain = ""
    for section in track.get("sections", []):
        for m in section.get("metadata", []):
            meta[m.get("title")] = m.get("text", "")
        if section.get("type") == "LYRICS":
            plain = "\n".join(section.get("text", []))
    images = track.get("images", {})
    cover = (images.get("coverarthq") or images.get("coverart") or "").replace("400x400", "1000x1000")
    matches = result.get("matches") or [{}]
    return {
        "title": track.get("title", ""),
        "artist": track.get("subtitle", ""),
        "album": meta.get("Album", ""),
        "label": meta.get("Label", ""),
        "year": meta.get("Released", ""),
        "genre": (track.get("genres") or {}).get("primary", ""),
        "cover": cover,
        "plain_lyrics": plain,
        # Hoe ver het nummer was aan het begin van de opname
        "offset": float(matches[0].get("offset") or 0),
    }


# ---------- extra info opzoeken ----------

async def get_json(session, url, params=None):
    try:
        async with session.get(url, params=params, headers=UA, timeout=HTTP_TIMEOUT) as r:
            if r.status == 200:
                return await r.json(content_type=None)
    except Exception as e:
        print("Opzoeken mislukt:", url, e)
    return None


def parse_lrc(text: str) -> list:
    lines = []
    for raw in (text or "").splitlines():
        stamps = re.findall(r"\[(\d+):(\d+(?:\.\d+)?)\]", raw)
        words = re.sub(r"\[[^\]]*\]", "", raw).strip()
        for mm, ss in stamps:
            lines.append({"t": int(mm) * 60 + float(ss), "text": words})
    return sorted(lines, key=lambda l: l["t"])


async def lookup_itunes(session, info):
    data = await get_json(session, "https://itunes.apple.com/search", {
        "term": f"{info['artist']} {info['title']}", "entity": "song", "limit": 10})
    results = (data or {}).get("results", [])
    if not results:
        return {}
    title = info["title"].lower()
    best = next((r for r in results if r.get("trackName", "").lower() == title), results[0])
    art = best.get("artworkUrl100", "").replace("100x100bb", "1200x1200bb")
    return {
        "duration": (best.get("trackTimeMillis") or 0) / 1000,
        "year": (best.get("releaseDate") or "")[:4],
        "genre": best.get("primaryGenreName", ""),
        "album": best.get("collectionName", ""),
        "track_number": best.get("trackNumber") or 0,
        "track_count": best.get("trackCount") or 0,
        "cover": art,
    }


async def lookup_lyrics(session, info):
    params = {"artist_name": info["artist"], "track_name": info["title"]}
    if info.get("album"):
        params["album_name"] = info["album"]
    if info.get("duration"):
        params["duration"] = round(info["duration"])
    hit = await get_json(session, "https://lrclib.net/api/get", params)
    if not hit:
        found = await get_json(session, "https://lrclib.net/api/search",
                               {"artist_name": info["artist"], "track_name": info["title"]})
        if found:
            hit = next((f for f in found if f.get("syncedLyrics")), found[0])
    if not hit:
        return {}
    return {"lyrics": parse_lrc(hit.get("syncedLyrics") or ""),
            "plain_lyrics": hit.get("plainLyrics") or "",
            "lrc_duration": hit.get("duration") or 0}


async def lookup_bio(session, artist):
    for lang in ("nl", "en"):
        data = await get_json(session,
                              f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{quote(artist.replace(' ', '_'))}")
        if data and data.get("type") == "standard" and data.get("extract"):
            return {"bio": data["extract"], "bio_source": f"Wikipedia ({lang})"}
    return {}


async def enrich(info: dict) -> dict:
    out = dict(EMPTY_TRACK)
    out.update({k: v for k, v in info.items() if v})
    async with aiohttp.ClientSession() as session:
        itunes, bio = await asyncio.gather(lookup_itunes(session, info), lookup_bio(session, info["artist"]))
        # Shazam-hoes en -album houden voorrang, iTunes vult aan
        for k, v in itunes.items():
            if v and (not out.get(k) or k in ("duration", "track_number", "track_count")):
                out[k] = v
        lyrics = await lookup_lyrics(session, out)
    if lyrics.get("lyrics"):
        out["lyrics"] = lyrics["lyrics"]
    if lyrics.get("plain_lyrics"):
        out["plain_lyrics"] = lyrics["plain_lyrics"]
    if not out["duration"] and lyrics.get("lrc_duration"):
        out["duration"] = lyrics["lrc_duration"]
    out.update(bio)
    if out["year"]:
        out["year"] = str(out["year"])[:4]
    return out


# ---------- hoofdlus ----------

def set_idle():
    if state["playing"]:
        state.update(playing=False, updated=time.time())
        print("Geen muziek meer, scherm gaat naar rust.")


async def listen_loop():
    global last_match
    from shazamio import Shazam
    shazam = Shazam()
    while True:
        wait = INTERVAL
        try:
            started = time.time()
            audio = await asyncio.to_thread(record)
            rms = float(np.sqrt(np.mean(audio ** 2)))
            if rms < SILENCE_RMS:
                if time.time() - last_match > IDLE_AFTER:
                    set_idle()
                await asyncio.sleep(3)
                continue
            info = parse_shazam(await shazam.recognize(to_wav(audio)))
            if info:
                last_match = time.time()
                offset = info.pop("offset")
                same = state["playing"] and info["title"] == state["title"] and info["artist"] == state["artist"]
                if not same:
                    print(f"Nu: {info['artist']} - {info['title']}")
                    state.update(playing=True, **(await enrich(info)))
                state.update(playing=True, offset=offset, matched_at=started, updated=time.time())
                # Niet steeds opnieuw luisteren tijdens hetzelfde nummer
                if state["duration"]:
                    left = state["duration"] - (offset + time.time() - started)
                    wait = max(INTERVAL, min(left - 5, 90))
            elif time.time() - last_match > IDLE_AFTER:
                set_idle()
        except Exception as e:  # netwerk- of audiofout: even wachten en opnieuw
            print("Fout:", e)
        await asyncio.sleep(wait)


DEMO_LYRICS = """[00:00.00] ♪
[00:08.50] Hier zou de songtekst staan
[00:13.00] Regel voor regel, op de maat van de plaat
[00:18.20] De huidige regel licht op
[00:23.40] En de tekst schuift vanzelf omhoog
[00:28.80] Net als in Apple Music
[00:34.00] Tik op een regel niet, dit is maar een demo
[00:39.50] ♪
[00:46.00] Tweede couplet begint hier
[00:51.20] Met nog wat regels om te scrollen
[00:56.60] Zodat je ziet hoe het beweegt
[01:02.00] Tot het einde van het nummer"""


async def demo_loop():
    state.update(EMPTY_TRACK)
    state.update(
        playing=True, title="Demo Song", artist="The Turntables", album="Side A Sessions",
        cover="/static/demo-cover.svg", duration=214, year="1977", genre="Rock", label="Demo Records",
        track_number=3, track_count=10, lyrics=parse_lrc(DEMO_LYRICS),
        bio="The Turntables zijn een fictieve band die alleen bestaat om dit scherm te laten zien. "
            "Hier komt in het echt een korte beschrijving van de artiest vanaf Wikipedia.",
        bio_source="Wikipedia (nl)", offset=24, matched_at=time.time(), updated=time.time())
    while True:
        await asyncio.sleep(3600)


# ---------- web ----------

async def api_state(_):
    s = dict(state)
    # Positie in het nummer op dit moment, zodat de klok van de iPad niet hoeft te kloppen
    s["position"] = state["offset"] + (time.time() - state["matched_at"]) if state["playing"] else 0
    return web.json_response(s)


async def index(_):
    return web.FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))


async def start_background(app):
    app["listener"] = asyncio.create_task(demo_loop() if DEMO else listen_loop())


def main():
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/api/state", api_state)
    app.router.add_static("/static", os.path.join(os.path.dirname(__file__), "static"))
    app.on_startup.append(start_background)
    print(f"Open http://<ip-van-deze-computer>:{PORT} op de iPad" + (" (demo-modus)" if DEMO else ""))
    web.run_app(app, port=PORT, print=None)


if __name__ == "__main__":
    main()
