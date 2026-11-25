# Stage 2 Knowledge Validation & Baseline Evaluation — Fortis

## 1. Evaluation Methodology & Test Suite
To systematically evaluate Fortis's baseline retrieval and generation quality prior to engineering modifications, a reproducible 30-case benchmark suite (`evaluation/benchmark_dataset.json`) was constructed and executed via `evaluation/run_benchmark.py`[cite: 22, 23].

- **Inference & Retrieval Stack:** Local open-source GGUF weights via `llama-cpp-python` (`max_tokens=300`, `temperature=0.2`)[cite: 19] evaluated across all three built-in `RAG_LEVEL` modes (`basic`, `standard`, `full`)[cite: 9].
- **Test Categories (30 Total Cases):**[cite: 23]
  1. `framework_mapping` (8 cases, `grc` role): NIST CSF, OWASP Top 10, and CIS Controls queries[cite: 23].
  2. `cti_ioc_triage` (8 cases, `cti` role): CVE advisories (`CVE-2024-3094`, `CVE-2024-21762`, `CVE-2023-44487`), MITRE ATT&CK tactics, and IPv4 C2 triage (`185.220.101.4`)[cite: 23].
  3. `doc_config_review` (8 cases, `code` role): Vulnerability analysis against an ingested baseline configuration file (`PermitRootLogin yes`, `TLSv1.0`, `0.0.0.0/0`, hardcoded `API_KEY`, `logging = off`)[cite: 23].
  4. `out_of_scope` (6 cases, `general` role): Non-cybersecurity prompts verifying guardrail precision[cite: 23].

### Metrics Tracked
- **Accuracy:** Proportion of cases passing both guardrail routing and expected domain concept verification[cite: 22].
- **False-Refusal Rate:** Proportion of the 24 in-scope cybersecurity queries wrongly rejected by `is_cybersecurity_related()`[cite: 6, 22].
- **Citation Grounding Rate:** Proportion of responses whose cited framework/CVE identifiers match retrieved context chunks[cite: 22].
- **Average Latency (s):** Mean end-to-end wall-clock response time per query[cite: 22].

---

## 2. Baseline Results (`RAG_LEVEL = full`)
Full baseline metrics are stored in `evaluation/results/baseline_summary.csv` and `evaluation/results/baseline_details.json`.

| RAG Mode | Total Cases | Accuracy | False-Refusal Rate | Citation Grounding | Avg Latency (s) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `full` (Baseline) | 30 | **0.467** (46.7%) | **0.375** (37.5%) | **0.933** (93.3%) | 30.978s |

---

## 3. Diagnosed Weaknesses & Root Causes
1. **Brittle Regex Guardrail (`37.5%` False-Refusal Rate):** `is_cybersecurity_related()` in `server/orchestration.py` relies on a static keyword list (`_CYBERSECURITY_KEYWORDS`)[cite: 6] lacking patterns for CVE identifiers, MITRE ATT&CK tactics/techniques, and IPv4 indicators. Consequently, 9 of 24 valid cybersecurity queries were rejected upstream before reaching the RAG pipeline[cite: 6].
2. **Absence of Threat-Intelligence & IR Playbook Knowledge:** `core/frameworks.py` seeds static compliance controls (`NIST_CSF`, `OWASP_TOP10`, `CIS_CONTROLS`)[cite: 10, 16], leaving Fortis without curated CISA KEV advisories or SOC incident-response playbooks for active threat triage.
3. **Ungrounded Citations (`6.7%` Ungrounded):** Without verified threat-intel context injected for CVE/IOC queries, the model occasionally cites unverified identifiers (`0.933` grounding rate).
4. **CPU Inference Latency Bottleneck:** Uncapped generation (`max_tokens=2048`)[cite: 19] required ~153s per query on CPU; capping evaluation generation at `max_tokens=300` reduced mean `full`-mode latency to `30.978s` while preserving core analytical content.

---

## 4. Stage 3 Design Requirements Derived from Baseline
These findings directly justify three targeted Stage 3 enhancements:
1. **Curated CISA KEV & MITRE ATT&CK SOC Playbook Corpus (`data/threat_intel_kev_attack.json`)** indexed into a dedicated hybrid ChromaDB + BM25 collection.
2. **Deterministic IOC/CVE/ATT&CK Entity Extractor (`core/threat_intel.py`)** integrated into `is_cybersecurity_related()`[cite: 6] to eliminate false-positive refusals.
3. **Autonomous CTI Context Enrichment (`core/rag.py`)** injecting matched advisories and extracted indicators into the prompt prior to generation.