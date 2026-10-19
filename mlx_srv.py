#!/usr/bin/env python3
"""Local AI translation on Apple Silicon through MLX (mlx_lm's OpenAI-compatible server), started by server.py.

Why not Ollama on a Mac: Gemma 3 uses sliding-window attention, and llama.cpp (inside Ollama) then cannot reuse a
cached prompt prefix — every line re-read the whole event description, twice (FR + ES): ~2.1 s per line on an
M2 Pro. mlx_lm keeps one prompt cache per language and decodes both languages together: ~1.2 s, same model.
Models load on the first request that names them. Exits when its parent (the LiveTranslate server) goes away.

    python mlx_srv.py --port 11441
"""
import os, sys, threading, time


def watch_parent(parent):
    while os.getppid() == parent:
        time.sleep(1)
    os._exit(0)


if __name__ == "__main__":
    threading.Thread(target=watch_parent, args=(os.getppid(),), daemon=True).start()
    from mlx_lm import server
    sys.argv = [sys.argv[0], "--host", "127.0.0.1", "--prompt-cache-size", "8", "--decode-concurrency", "2",
                "--prompt-concurrency", "2", "--max-tokens", "300"] + sys.argv[1:]
    server.main()
