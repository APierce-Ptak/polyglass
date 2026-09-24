"""
Download sizes of the offline translation models, shown in the language bar and in setup.

Standard library only: the setup wizard uses it before the app's packages are installed.
Sizes come from the model index Argos Translate uses and a HEAD request per model file
(just the size, not the file). Without internet everything returns empty, and callers
fall back to not showing a size.
"""
import json
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

INDEX_URL = "https://raw.githubusercontent.com/argosopentech/argospm-index/main/index.json"
HEADERS = {"User-Agent": "Polyglass"}      # the model server refuses requests without one


def packages_dir():
    """Where Argos Translate keeps installed models (same rules as argostranslate.settings)."""
    if os.getenv("ARGOS_PACKAGES_DIR"):
        return Path(os.environ["ARGOS_PACKAGES_DIR"])
    share = Path(os.getenv("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return share / "argos-translate" / "packages"


def installed_pairs(folder=None):
    """{(from_code, to_code)} of the installed translation models."""
    pairs = set()
    for meta in Path(folder or packages_dir()).glob("*/metadata.json"):
        try:
            m = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if m.get("type", "translate") == "translate" and "from_code" in m:
            pairs.add((m["from_code"], m["to_code"]))
    return pairs


def load_index(timeout=10):
    """{(from_code, to_code): download url} for every model in the index, or {} offline."""
    try:
        with urllib.request.urlopen(urllib.request.Request(INDEX_URL, headers=HEADERS),
                                    timeout=timeout) as r:
            index = json.load(r)
    except (OSError, ValueError):
        return {}
    out = {}
    for p in index:
        url = next((l for l in p.get("links", []) if l.startswith("http")), None)
        if p.get("type", "translate") == "translate" and url:
            out[(p["from_code"], p["to_code"])] = url
    return out


def fetch_sizes(index, codes, timeout=8):
    """{(from, to): bytes} for the models between languages in `codes`."""
    wanted = [(pair, url) for pair, url in index.items() if pair[0] in codes and pair[1] in codes]

    def size(item):
        pair, url = item
        try:
            req = urllib.request.Request(url, method="HEAD", headers=HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                n = int(r.headers.get("Content-Length") or 0)
            return pair, n or None
        except (OSError, ValueError):
            return pair, None

    with ThreadPoolExecutor(8) as ex:
        return {pair: n for pair, n in ex.map(size, wanted) if n}


def needed(src, dst, available, installed):
    """Models to download so src -> dst works, the way the app gets them: nothing if a route
    is installed (direct or through English), else the direct model, else the two through
    English. None when the index has no route."""
    if src == dst or (src, dst) in installed or {(src, "en"), ("en", dst)} <= installed:
        return []
    if (src, dst) in available:
        return [(src, dst)]
    legs = [(src, "en"), ("en", dst)]
    if not all(leg in available for leg in legs):
        return None
    return [leg for leg in legs if leg not in installed]


def total(models, sizes):
    """Total bytes for `models`, or None when any size is unknown."""
    if models is None or any(m not in sizes for m in models):
        return None
    return sum(sizes[m] for m in models)


def label(nbytes):
    """'240 MB' / '1.2 GB'."""
    mb = nbytes / 1_000_000
    return f"{mb / 1000:.1f} GB" if mb >= 1000 else f"{max(1, round(mb))} MB"
