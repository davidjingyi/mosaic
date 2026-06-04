"""Medical query expansion using synonym dictionaries and MedCT."""
import json
import logging
import urllib.request
from pathlib import Path
from typing import Set

from app.config import TerminologyConfig
from app.core.interfaces import QueryExpander

logger = logging.getLogger(__name__)

MEDCT_WARNINGS: list[str] = []

MEDCT_FILES = {
    "body.json": "body_sctid_syn-enzh.json",
    "findings.json": "find_sctid_syn-enzh.json",
    "procedures.json": "proc_sctid_syn-enzh.json",
}


class MedicalQueryExpander(QueryExpander):
    """Expand queries with medical synonyms from builtin rules or MedCT."""

    def __init__(self, config: TerminologyConfig):
        self.config = config
        self._synonyms: dict[str, list[str]] = {}
        self._loaded = False
        self._load()

    def _load(self) -> None:
        source = self.config.source
        if source == "medct":
            self._load_medct()
        elif source == "builtin":
            self._load_builtin(self.config.custom_rules)
        elif source == "none":
            logger.info("Query expansion disabled")
        else:
            logger.warning("Unknown terminology source '%s', falling back to builtin", source)
            self._load_builtin(self.config.custom_rules)

    def _load_medct(self) -> None:
        medct_path = Path(self.config.medct_path)
        if self.config.auto_download:
            self._ensure_medct_downloaded(medct_path)

        if not medct_path.exists():
            logger.warning("MedCT not found at %s, using builtin rules", medct_path)
            self._load_builtin(self.config.custom_rules)
            return

        total = 0
        for local_name in MEDCT_FILES.keys():
            fpath = medct_path / local_name
            if not fpath.exists():
                continue
            try:
                count = self._parse_medct_file(fpath)
                total += count
                logger.info("MedCT loaded %s: %d groups", local_name, count)
            except Exception as e:
                logger.error("Error loading MedCT %s: %s", local_name, e)
                MEDCT_WARNINGS.append(f"MedCT {local_name}: {e}")

        self._merge_custom_rules(self.config.custom_rules)
        self._loaded = total > 0
        logger.info("MedCT ready: %d terms loaded", len(self._synonyms))

    def _parse_medct_file(self, fpath: Path) -> int:
        with open(fpath, "r", encoding="utf-8") as f:
            data = json.load(f)
        count = 0
        for sctid, term_data in data.items():
            # MedCT files come in two formats:
            #   dict: {"zh": [...], "en": [...]}
            #   list: ["term1", "term2", ...]
            if isinstance(term_data, list):
                all_terms = [t.strip() for t in term_data if isinstance(t, str) and t.strip()]
            else:
                zh = [t.strip() for t in term_data.get("zh", []) if t.strip()]
                en = [t.strip() for t in term_data.get("en", []) if t.strip()]
                all_terms = zh + en
            if len(all_terms) < 2:
                continue
            for term in all_terms:
                tlower = term.lower()
                others = [o for o in all_terms if o.lower() != tlower]
                existing = set(self._synonyms.get(tlower, []))
                existing.update(others)
                self._synonyms[tlower] = list(existing)
            count += 1
        return count

    def _ensure_medct_downloaded(self, medct_path: Path) -> None:
        medct_path.mkdir(parents=True, exist_ok=True)
        base = self.config.medct_download_url
        for local_name, remote_name in MEDCT_FILES.items():
            fpath = medct_path / local_name
            if fpath.exists() and fpath.stat().st_size > 1000:
                # Validate existing file is parseable JSON
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        json.load(f)
                    continue
                except Exception:
                    logger.warning("MedCT %s is corrupted, re-downloading", local_name)
                    fpath.unlink(missing_ok=True)

            url = f"{base}/{remote_name}?download=true"
            logger.info("Downloading MedCT: %s", local_name)
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=120) as response:
                    data = response.read()
                # Validate before writing
                json.loads(data.decode("utf-8"))
                with open(fpath, "wb") as f:
                    f.write(data)
                logger.info("MedCT %s downloaded (%d bytes)", local_name, len(data))
            except Exception as e:
                logger.error("Failed to download %s: %s", local_name, e)

    def _load_builtin(self, rules: dict[str, list[str]]) -> None:
        self._synonyms = {}
        self._merge_custom_rules(rules)
        self._loaded = len(self._synonyms) > 0
        logger.info("Builtin terminology loaded: %d terms", len(self._synonyms))

    def _merge_custom_rules(self, rules: dict[str, list[str]]) -> None:
        for key, values in rules.items():
            klower = key.lower()
            existing = set(self._synonyms.get(klower, []))
            existing.update(values)
            self._synonyms[klower] = list(existing)
            for val in values:
                vlower = val.lower()
                rev = set(self._synonyms.get(vlower, []))
                rev.add(key)
                self._synonyms[vlower] = list(rev)

    def expand(self, query: str) -> str:
        if not self._loaded or not self.config.enabled:
            return query

        # Simple LRU-style result cache to avoid repeated expansion of same query
        cache_key = query.lower()
        if hasattr(self, '_expand_cache') and cache_key in self._expand_cache:
            return self._expand_cache[cache_key]

        expanded: list[str] = [query]
        added: Set[str] = {query.lower()}
        qlower = query.lower()

        def _add(term: str) -> None:
            t = term.strip()
            tlower = t.lower()
            if tlower not in added and len(t) >= 2:
                added.add(tlower)
                expanded.append(t)

        # Whole phrase match
        if qlower in self._synonyms:
            for syn in self._synonyms[qlower]:
                _add(syn)

        # Substring match: only check terms that appear in query
        # (avoids full-dict scan when many terms don't match)
        for term, syns in self._synonyms.items():
            if term in qlower and term != qlower and len(term) >= 2:
                for syn in syns:
                    _add(syn)

        # Jieba token match
        try:
            import jieba
            for token in jieba.cut(query):
                tlower = token.lower().strip()
                if tlower in self._synonyms and len(token.strip()) >= 2:
                    for syn in self._synonyms[tlower]:
                        _add(syn)
        except ImportError:
            pass

        if len(expanded) > 1:
            result = " ".join(expanded)
            logger.debug("Query expanded: '%s' -> '%s...'", query, result[:120])
        else:
            result = query

        # Cache result
        if not hasattr(self, '_expand_cache'):
            self._expand_cache: dict[str, str] = {}
        self._expand_cache[cache_key] = result
        # Limit cache size
        if len(self._expand_cache) > 1000:
            self._expand_cache.clear()
        return result
