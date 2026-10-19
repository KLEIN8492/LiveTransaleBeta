"""Argos Translate without its paragraph splitter.

argostranslate.translate splits text into sentences with Stanza/spaCy, which loads PyTorch, spaCy, SciPy and Thinc
(~1 GB resident) — on a 16 GB Mac that memory pushed Whisper into swap mid-show. LiveTranslate only ever sends ONE
sentence, so this calls the installed packages directly: the package's own tokenizer (SentencePiece or BPE) and its
CTranslate2 model, with Argos' exact decoding settings (beam 4, length penalty 0.2, target prefix). Same output.

    from argos_lite import translate; translate("Good evening.", "en", "fr")
"""
import re, threading

_lock, _pkgs, _models = threading.Lock(), None, {}


def _packages():
    global _pkgs
    if _pkgs is None:
        import argostranslate.package as p  # the package index only: no Stanza/spaCy/torch
        _pkgs = {(x.from_code, x.to_code): x for x in p.get_installed_packages()}
    return _pkgs


def _model(pkg):
    key = (pkg.from_code, pkg.to_code)
    with _lock:
        if key not in _models:
            import ctranslate2
            _models[key] = ctranslate2.Translator(str(pkg.package_path / "model"), device="cpu",
                                                  inter_threads=1, intra_threads=0, compute_type="auto")
    return _models[key]


def _one(text, pkg):
    tokens = pkg.tokenizer.encode(text)
    prefix = [[pkg.target_prefix]] if getattr(pkg, "target_prefix", "") else None
    res = _model(pkg).translate_batch([tokens], target_prefix=prefix, replace_unknowns=True, beam_size=4,
                                      num_hypotheses=1, length_penalty=0.2)
    out = pkg.tokenizer.decode(res[0].hypotheses[0])
    if prefix and out.startswith(pkg.target_prefix):
        out = out[len(pkg.target_prefix):]
    return out[1:] if out.startswith(" ") else out


# sentence ends: . ! ? … (optionally closing quote/bracket) then space and an upper-case letter, digit or opening mark
_SPLIT = re.compile(r"(?<=[.!?…])[\"'”»)]*\s+(?=[\"'“«(¿¡]?[A-ZÀ-ÖØ-Þ0-9])")


def _route(text, frm, to):
    pk = _packages()
    if (frm, to) in pk:
        return _one(text, pk[(frm, to)])
    if (frm, "en") in pk and ("en", to) in pk:
        return _one(_one(text, pk[(frm, "en")]), pk[("en", to)])
    raise LookupError(f"no offline translation package {frm}->{to}")


def translate(text, frm, to):
    """Sentence by sentence (as Argos does — whole multi-sentence lines made the models drop sentences), each by the
    direct package, else pivoting through English. Raises if no route is installed."""
    if frm == to or not text.strip():
        return text
    parts = [p for p in _SPLIT.split(text.strip()) if p.strip()]
    return " ".join(_route(p, frm, to) for p in parts)


def installed_pairs():
    return sorted(f"{a}-{b}" for a, b in _packages())
