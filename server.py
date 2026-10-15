"""Now Playing: luistert via een microfoon naar de platenspeler,
herkent het nummer met Shazam en toont de hoes op een webpagina."""
import asyncio
import io
import os
import time
import wave

import numpy as np
import sounddevice as sd
from aiohttp import web
from shazamio import Shazam

SAMPLE_RATE = 44100
RECORD_SECONDS = int(os.environ.get("RECORD_SECONDS", 8))
INTERVAL = int(os.environ.get("INTERVAL", 20))
SILENCE_RMS = float(os.environ.get("SILENCE_RMS", 0.005))
IDLE_AFTER = int(os.environ.get("IDLE_AFTER", 90))
PORT = int(os.environ.get("PORT", 8080))
DEVICE = os.environ.get("AUDIO_DEVICE")  # naam of index, leeg = standaard

state = {"playing": False, "title": "", "artist": "", "album": "", "cover": "", "updated": 0}
last_match = 0.0


def record() -> np.ndarray:
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


def parse(result: dict) -> dict | None:
    track = result.get("track")
    if not track:
        return None
    album = ""
    for section in track.get("sections", []):
        for meta in section.get("metadata", []):
            if meta.get("title") == "Album":
                album = meta.get("text", "")
    images = track.get("images", {})
    cover = images.get("coverarthq") or images.get("coverart") or ""
    # Grotere hoes opvragen dan de standaard 400x400
    cover = cover.replace("400x400", "1000x1000")
    return {"title": track.get("title", ""), "artist": track.get("subtitle", ""),
            "album": album, "cover": cover}


def set_idle():
    if state["playing"]:
        state.update(playing=False, updated=time.time())
        print("Geen muziek meer, scherm gaat naar rust.")


async def listen_loop():
    global last_match
    shazam = Shazam()
    while True:
        try:
            audio = await asyncio.to_thread(record)
            rms = float(np.sqrt(np.mean(audio ** 2)))
            if rms < SILENCE_RMS:
                if time.time() - last_match > IDLE_AFTER:
                    set_idle()
                await asyncio.sleep(3)
                continue
            info = parse(await shazam.recognize(to_wav(audio)))
            if info:
                last_match = time.time()
                if info["title"] != state["title"] or not state["playing"]:
                    print(f"Nu: {info['artist']} - {info['title']}")
                state.update(playing=True, updated=time.time(), **info)
            elif time.time() - last_match > IDLE_AFTER:
                set_idle()
        except Exception as e:  # netwerk- of audiofout: even wachten en opnieuw
            print("Fout:", e)
        await asyncio.sleep(INTERVAL)


async def api_state(_):
    return web.json_response(state)


async def index(_):
    return web.FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))


async def start_background(app):
    app["listener"] = asyncio.create_task(listen_loop())


def main():
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/api/state", api_state)
    app.on_startup.append(start_background)
    print(f"Open http://<ip-van-deze-computer>:{PORT} op de iPad")
    web.run_app(app, port=PORT)


if __name__ == "__main__":
    main()
