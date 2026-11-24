"""
Automated Stage 2 & Stage 3 RAG Benchmark Runner for Fortis.
Evaluates basic, standard, and full RAG modes using Orchestrator.
"""
import argparse
import csv
import json
import os
import re
import sys
import tempfile
import time
from typing import Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import settings
from core import store, vectorstore, rag
from core.frameworks import seed_all_frameworks
from server.orchestration import Orchestrator, is_offtopic_refusal


def _citation_grounded(reply: str, retrieved_context: str) -> bool:
    """Checks whether framework/CVE citations in reply appear in retrieved context."""
    citations = re.findall(
        r"\b(?:CVE-\d{4}-\d{4,7}|(?:GV|ID|PR|DE|RS|RC)\.[A-Z]{2}|A\d{2}:\d{4}|CIS\s*\d+)\b",
        reply,
    )
    if not citations:
        return True
    ctx_lower = retrieved_context.lower()
    return any(c.lower() in ctx_lower for c in citations)


def run_mode(rag_level: str, dataset: List[Dict]) -> Dict:
    settings.rag_level = rag_level
    orch = Orchestrator()
    eng = orch.create_engagement("BenchmarkCorp", f"Eval-{rag_level}-{int(time.time())}")
    eng_id = eng["id"]

    doc_case = next((c for c in dataset if c.get("requires_doc") and c.get("reference_doc_content")), None)
    if doc_case:
        fd, tmp_path = tempfile.mkstemp(suffix=".txt", prefix="fortis_eval_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(doc_case["reference_doc_content"])
            orch.ingest_file(tmp_path, eng_id)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    results = []
    total = len(dataset)
    for idx, case in enumerate(dataset, start=1):
        t0 = time.perf_counter()
        turn = orch.chat_turn(case["prompt"], history=[], role=case.get("role"))
        latency = round(time.perf_counter() - t0, 3)

        reply = turn.get("text", "")
        refused = (turn.get("kind") == "offtopic") or is_offtopic_refusal(reply)

        if case["expect_refusal"]:
            passed = refused
            false_refusal = False
            grounded = True
        else:
            false_refusal = refused
            keywords = case.get("expected_keywords") or (
                [case["expected_concept"]] if case.get("expected_concept") else []
            )
            keywords = [k for k in keywords if k and k != "<conceptual_placeholder>"]
            concept_hit = (not keywords) or any(k.lower() in reply.lower() for k in keywords)
            passed = (not refused) and concept_hit
            retrieved_ctx = "" if refused else rag.build_context_block(eng_id, case["prompt"])
            grounded = True if refused else _citation_grounded(reply, retrieved_ctx)

        print(
            f"  [{idx}/{total}] {case['id']} ({case['category']}): "
            f"passed={passed}, refused={refused}, grounded={grounded}, {latency}s",
            flush=True,
        )
        results.append({
            "id": case["id"],
            "category": case["category"],
            "rag_level": rag_level,
            "passed": passed,
            "false_refusal": false_refusal,
            "grounded": grounded,
            "latency_s": latency,
            "reply_preview": reply[:200],
        })

    vectorstore.clear_engagement(eng_id)
    store.delete_engagement(eng_id)

    in_scope = [r for r in results if not next(c["expect_refusal"] for c in dataset if c["id"] == r["id"])]
    summary = {
        "rag_level": rag_level,
        "total_cases": total,
        "accuracy": round(sum(r["passed"] for r in results) / max(1, total), 3),
        "false_refusal_rate": round(sum(r["false_refusal"] for r in in_scope) / max(1, len(in_scope)), 3),
        "grounding_rate": round(sum(r["grounded"] for r in results) / max(1, total), 3),
        "avg_latency_s": round(sum(r["latency_s"] for r in results) / max(1, total), 3),
    }
    return {"summary": summary, "details": results}


def main():
    parser = argparse.ArgumentParser(description="Run Fortis RAG benchmark suite")
    parser.add_argument("--dataset", default=os.path.join("evaluation", "benchmark_dataset.json"))
    parser.add_argument("--modes", nargs="+", default=["basic", "standard", "full"])
    parser.add_argument("--out-dir", default=os.path.join("evaluation", "results"))
    parser.add_argument("--limit", type=int, default=0, help="Optional cap on number of test cases")
    parser.add_argument("--max-tokens", type=int, default=300, help="Max generation tokens per query")
    args = parser.parse_args()

    store.init_db()
    seed_all_frameworks()

    import engine as _engine
    _orig_chat = _engine.chat

    async def _fast_eval_chat(messages, temperature=0.2, json_mode=False, max_tokens=None):
        return await _orig_chat(
            messages, temperature=temperature, json_mode=json_mode, max_tokens=max_tokens or args.max_tokens
        )

    _engine.chat = _fast_eval_chat

    with open(args.dataset, "r", encoding="utf-8") as f:
        cases = json.load(f)["cases"]
    if args.limit > 0:
        cases = cases[:args.limit]

    os.makedirs(args.out_dir, exist_ok=True)
    all_summaries = []
    all_details = []

    for mode in args.modes:
        print(f"[..] Running RAG mode: {mode}", flush=True)
        out = run_mode(mode, cases)
        all_summaries.append(out["summary"])
        all_details.extend(out["details"])
        print(f"[ok] {mode}: {out['summary']}", flush=True)

    summary_csv = os.path.join(args.out_dir, "summary.csv")
    with open(summary_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(all_summaries[0].keys()))
        writer.writeheader()
        writer.writerows(all_summaries)

    details_json = os.path.join(args.out_dir, "details.json")
    with open(details_json, "w", encoding="utf-8") as f:
        json.dump(all_details, f, indent=2)

    print(f"Saved summary to {summary_csv} and details to {details_json}")


if __name__ == "__main__":
    main()