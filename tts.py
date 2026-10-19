"""Natural voice for the audience's "Listen" button: Kokoro (82M-parameter neural TTS, ONNX, offline, CPU).

Each line is synthesised once per language, on the first phone's request, and every other phone listening in that
language gets the same MP3 from the cache. Phones whose language has no natural voice keep their own browser voice.
~0.5 s per sentence on a laptop CPU (int8 model), so it runs beside Whisper without a GPU.

Model files (installed by setup): <models>/kokoro/kokoro-v1.0.int8.onnx + voices-v1.0.bin
"""
import collections, io, os, shutil, threading, time
from concurrent.futures import Future, ThreadPoolExecutor

import numpy as np

# language -> (Kokoro language code, female voice, male voice). Voices graded best in the Kokoro v1.0 card.
VOICES = {
    "en": ("en-us", "af_heart", "am_michael"),
    "fr": ("fr-fr", "ff_siwis", "ff_siwis"),  # Kokoro has one French voice
    "es": ("es", "ef_dora", "em_alex"),
    "it": ("it", "if_sara", "im_nicola"),
    "pt": ("pt-br", "pf_dora", "pm_alex"),
}
HELLO = {"en": "Voice on.", "fr": "Voix activée.", "es": "Voz activada.", "it": "Voce attiva.", "pt": "Voz ativada."}
MODEL, VOICES_BIN = "kokoro-v1.0.int8.onnx", "voices-v1.0.bin"


def model_dir(data):
    return os.path.join(data, "models", "kokoro")


def available(data):
    d = model_dir(data)
    return os.path.exists(os.path.join(d, MODEL)) and os.path.exists(os.path.join(d, VOICES_BIN))


def _espeak_data(data):
    """espeak-ng (used for phonemes) silently ignores a data path longer than ~160 characters and then exits the
    whole process looking for its build machine's path. Deep install folders hit that: use a short copy instead."""
    import espeakng_loader
    src = espeakng_loader.get_data_path()
    if len(src) < 150:
        return src
    dst = os.path.join(model_dir(data), "espeak-ng-data")
    if len(dst) >= 150:
        raise RuntimeError("install folder path too long for the voice engine")
    if not os.path.exists(os.path.join(dst, "phontab")):
        shutil.copytree(src, dst, dirs_exist_ok=True)
    return dst


class Voice:
    def __init__(self, data, log=print):
        self.data, self.log = data, log
        self.k, self.err = None, ""
        self.pool = ThreadPoolExecutor(1)  # one sentence at a time: never competes with Whisper for every core
        self.lock = threading.Lock()
        self.cache = collections.OrderedDict()  # (id, lang, text) -> Future[bytes]

    def langs(self):
        return sorted(VOICES) if available(self.data) else []

    def _load(self):
        if self.k is None:
            from kokoro_onnx import EspeakConfig, Kokoro
            import onnxruntime as ort
            d = model_dir(self.data)
            t = time.time()
            so = ort.SessionOptions()
            so.intra_op_num_threads = max(2, min(4, (os.cpu_count() or 4) // 2))  # leave cores for speech recognition
            sess = ort.InferenceSession(os.path.join(d, MODEL), so, providers=["CPUExecutionProvider"])
            self.k = Kokoro.from_session(sess, os.path.join(d, VOICES_BIN),
                                         espeak_config=EspeakConfig(data_path=_espeak_data(self.data)))
            self.log(f"[voice] natural voice ready ({time.time() - t:.1f}s)")
        return self.k

    def warm(self):
        """Load the model in the background when a session starts, so the first listener doesn't wait for it."""
        if available(self.data):
            self.pool.submit(self._synth, "Ready.", "en", "female")

    def _synth(self, text, lang, gender):
        code, f, m = VOICES[lang]
        k = self._load()
        t = time.time()
        audio, sr = k.create(text, voice=m if gender == "male" else f, speed=1.0, lang=code)
        mp3 = encode_mp3(audio, sr)
        self.log(f"[voice] {lang} {time.time() - t:.2f}s for {len(audio) / sr:.1f}s of speech")
        return mp3

    def get(self, key, text, lang, gender="female"):
        """Future of the MP3 for this text. Same key + text = same Future: 50 phones cost one synthesis."""
        if lang not in VOICES or not text.strip() or not available(self.data):
            fut = Future()
            fut.set_result(None)
            return fut
        k = (key, lang, gender, text)
        with self.lock:
            fut = self.cache.get(k)
            if fut is None:
                fut = self.cache[k] = self.pool.submit(self._synth, text[:600], lang, gender)
                while len(self.cache) > 300:
                    self.cache.popitem(last=False)
            else:
                self.cache.move_to_end(k)
        return fut


def encode_mp3(audio, sr):
    """48 kbit/s mono MP3: ~6 KB per second of speech (a WAV would be 8× that over the audience Wi-Fi), and the
    one format every phone browser plays."""
    import lameenc
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
    enc = lameenc.Encoder()
    enc.set_bit_rate(48)
    enc.set_in_sample_rate(sr)
    enc.set_channels(1)
    enc.set_quality(2)
    out = io.BytesIO()
    out.write(enc.encode(pcm))
    out.write(enc.flush())
    return out.getvalue()
