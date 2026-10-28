"""Generate the short feedback sounds used by the kiosk (written to hadir/resources/sounds)."""

import math
import struct
import wave
from pathlib import Path

RATE = 22050
OUT = Path(__file__).resolve().parents[1] / "hadir" / "resources" / "sounds"


def tone(freqs, dur, vol=0.45):
    n = int(RATE * dur)
    out = []
    for i in range(n):
        t = i / RATE
        env = min(1.0, i / (0.01 * RATE)) * math.exp(-3.2 * t / dur)
        s = sum(math.sin(2 * math.pi * f * t) for f in freqs) / len(freqs)
        out.append(vol * env * s)
    return out


def write(name, samples):
    OUT.mkdir(parents=True, exist_ok=True)
    with wave.open(str(OUT / name), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, s)) * 32767)) for s in samples))


if __name__ == "__main__":
    write("ok.wav", tone([880], 0.12) + tone([1318.5], 0.28))           # rising two-note chime
    write("warn.wav", tone([660], 0.18) + tone([660], 0.18))            # double beep
    write("bad.wav", tone([220, 233], 0.45, 0.5))                       # low buzz
    print("written to", OUT)
