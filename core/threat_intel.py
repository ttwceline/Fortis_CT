"""
Threat Intelligence & IOC/CVE/ATT&CK Extraction Module for Fortis.

Provides:
1. Deterministic entity extraction (CVEs, MITRE ATT&CK IDs/tactics, IPv4s,
   domains, SHA-256 hashes) for guardrail classification and query enrichment.
2. Hybrid dense + BM25 ingestion and retrieval over curated CISA KEV advisories
   and SOC Incident Response Playbooks.
"""
import json
import os
import re
from typing import Dict, List

import chromadb
from chromadb.config import Settings as ChromaSettings

from .config import settings
from . import bm25 as bm25_index
from .embeddings import embed_texts, embed_query

THREAT_INTEL_COLLECTION = "threat_intelligence"

_client = chromadb.PersistentClient(
    path=settings.chroma_persist_dir,
    settings=ChromaSettings(anonymized_telemetry=False),
)
_threat_intel = _client.get_or_create_collection(THREAT_INTEL_COLLECTION)

_CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.IGNORECASE)
_ATTCK_ID_RE = re.compile(r"\bT\d{4}(?:\.\d{3})?\b", re.IGNORECASE)
_CWE_RE = re.compile(r"\bCWE-\d{1,5}\b", re.IGNORECASE)
_IPV4_RE = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b")
_SHA256_RE = re.compile(r"\b[a-fA-F0-9]{64}\b")
_TACTIC_RE = re.compile(
    r"\b(initial\s+access|execution|persistence|privilege\s+escalation|"
    r"defense\s+evasion|credential\s+access|discovery|lateral\s+movement|"
    r"collection|command\s+and\s+control|exfiltration|impact|supply\s+chain|"
    r"lsass|smb|powershell|scheduled\s+tasks?|xz-utils|fortios)\b",
    re.IGNORECASE,
)


def extract_threat_entities(text: str) -> Dict[str, List[str]]:
    """Extracts structured cybersecurity indicators and identifiers from text."""
    if not text:
        return {"cves": [], "attck_ids": [], "cwes": [], "ipv4s": [], "sha256s": [], "tactics": []}
    return {
        "cves": sorted({m.upper() for m in _CVE_RE.findall(text)}),
        "attck_ids": sorted({m.upper() for m in _ATTCK_ID_RE.findall(text)}),
        "cwes": sorted({m.upper() for m in _CWE_RE.findall(text)}),
        "ipv4s": sorted(set(_IPV4_RE.findall(text))),
        "sha256s": sorted({m.lower() for m in _SHA256_RE.findall(text)}),
        "tactics": sorted({m.lower() for m in _TACTIC_RE.findall(text)}),
    }


def has_threat_entities(text: str) -> bool:
    """True when at least one concrete CVE, ATT&CK ID, CWE, IPv4, hash, or tactic is present."""
    entities = extract_threat_entities(text)
    return any(bool(v) for v in entities.values())


def seed_threat_intel(dataset_path: str | None = None, force: bool = False) -> int:
    """Ingests curated CISA KEV advisories and SOC IR playbooks into Chroma + BM25."""
    if dataset_path is None:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        dataset_path = os.path.join(root, "data", "threat_intel_kev_attack.json")

    if not os.path.isfile(dataset_path):
        return 0

    existing = _threat_intel.get()
    if existing["ids"]:
        if not force:
            return len(existing["ids"])
        _threat_intel.delete(ids=existing["ids"])

    with open(dataset_path, "r", encoding="utf-8") as f:
        records = json.load(f)

    if not records:
        return 0

    ids = [r["id"] for r in records]
    texts = [r["text"] for r in records]
    metadatas = [
        {
            "intel_id": r["id"],
            "source": r.get("source", "THREAT_INTEL"),
            "tactic": r.get("tactic", ""),
            "technique_id": r.get("technique_id", ""),
            "title": r.get("title", ""),
        }
        for r in records
    ]
    embeddings = embed_texts(texts)
    _threat_intel.add(ids=ids, embeddings=embeddings, documents=texts, metadatas=metadatas)
    bm25_index.mark_dirty(THREAT_INTEL_COLLECTION)
    return len(records)


def _rrf_fuse(dense_hits: List[Dict], bm25_hits: List[Dict], top_k: int, k: int = 60) -> List[Dict]:
    def _key(hit: Dict):
        return (hit["text"], tuple(sorted((hit["metadata"] or {}).items())))

    fused = {}
    for rank, hit in enumerate(dense_hits, start=1):
        fused.setdefault(_key(hit), {"hit": hit, "score": 0.0})
        fused[_key(hit)]["score"] += 1.0 / (k + rank)
    for rank, hit in enumerate(bm25_hits, start=1):
        fused.setdefault(_key(hit), {"hit": hit, "score": 0.0})
        fused[_key(hit)]["score"] += 1.0 / (k + rank)
    ranked = sorted(fused.values(), key=lambda entry: entry["score"], reverse=True)
    return [entry["hit"] for entry in ranked[:top_k]]


def query_threat_intel_hybrid(query: str, top_k: int = 3) -> List[Dict]:
    """Hybrid dense + BM25 retrieval over the threat intelligence collection."""
    count = _threat_intel.count()
    if count == 0:
        return []
    n_res = min(top_k * 3, count)
    dense_raw = _threat_intel.query(
        query_embeddings=[embed_query(query)],
        n_results=n_res,
    )
    docs = dense_raw.get("documents", [[]])[0]
    metas = dense_raw.get("metadatas", [[]])[0]
    dists = dense_raw.get("distances", [[]])[0] if dense_raw.get("distances") else [None] * len(docs)
    dense_hits = [{"text": d, "metadata": m, "distance": dist} for d, m, dist in zip(docs, metas, dists)]

    bm25_hits = bm25_index.search(
        THREAT_INTEL_COLLECTION,
        "_all",
        lambda: _threat_intel.get(),
        query,
        n_res,
    )
    return _rrf_fuse(dense_hits, bm25_hits, top_k, settings.bm25_rrf_k)