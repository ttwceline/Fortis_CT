# Stage 1 Deployment & Troubleshooting Log — Fortis Cybersecurity Advisor

## Environment & Hardware Baseline
- **Operating System:** Windows (Home/Desktop) with PowerShell
- **IDE / Runtime:** PyCharm with isolated virtual environment (`.venv`)
- **Inference Backend:** Local open-source GGUF weights via `llama-cpp-python` (CPU/CUDA auto-detection)[cite: 2, 19, 20]
- **Vector Store & Embeddings:** Local ChromaDB (`CHROMA_PERSIST_DIR`) + `BAAI/bge-small-en-v1.5` (`sentence-transformers`) and `rank-bm25`[cite: 1, 9, 16]

---

## Summary of Deployment Faults & Resolutions

| ID | Fault / Symptom | Root Cause | Resolution Applied | Verification |
| :--- | :--- | :--- | :--- | :--- |
| **DEP-01** | `setup.ps1` failed to locate a compatible prebuilt `llama-cpp-python` wheel on Python 3.13/3.14 and fell back to MSVC source compilation (`No prebuilt llama-cpp-python wheel`). | `setup.ps1` accepted Python `3.10`–`3.14` (`Python 3\.(1[0-4]\|[0-9])`)[cite: 2], whereas the upstream `abetlen.github.io/llama-cpp-python` wheel indices (`/whl/cu124` and `/whl/cpu`) only provide prebuilt Windows binary wheels for Python `3.10`–`3.12`. | Updated Python discovery in `setup.ps1` to prioritize and enforce `py -3.12`, `py -3.11`, and `py -3.10` (`Python 3\.1[0-2]\.`), removed the stale `.venv`, and rebuilt `.venv` cleanly on Python 3.12. | `setup.ps1` resolved `Python 3.12.x` and installed the prebuilt `llama-cpp-python` wheel cleanly without requiring MSVC/CMake. |
| **DEP-02** | PowerShell terminating error / warning noise during `pip uninstall` cleanup steps in `setup.ps1`. | `$ErrorActionPreference = "Stop"` was set globally at the top of `setup.ps1`[cite: 2], while `pip uninstall --yes llama_cpp_python` calls were executed outside the `Invoke-Native` helper, causing `stderr` warnings (`WARNING: Skipping llama_cpp_python...`) to surface under strict error handling. | Encapsulated all `pip uninstall` and `pip install` calls inside `Invoke-Native` helper functions (`Uninstall-LlamaSafe`) and reset `$global:LASTEXITCODE = 0` cleanly between fallback stages. | Cleanup and fallback wheel probes executed silently without premature PowerShell script termination. |
| **DEP-03** | False-positive `.llama-cuda-ok` hardware marker file created on CPU-only installations. | In Step 4 of `setup.ps1`, the `else` branch (executed when `llama_cpp` was already present) unconditionally created `.llama-cuda-ok`[cite: 2], which `engine/hardware.py` (`can_use_cuda()`) interprets as verified CUDA wheel support[cite: 18]. | Removed unconditional creation of `.llama-cuda-ok` from the `else` branch so the marker is written only after a verified CUDA wheel import succeeds. | `engine/hardware.py` accurately reports `backend_wheel` and offload capability matching the installed wheel. |
| **DEP-04** | PyCharm interpreter version mismatch after rebuilding `.venv`. | PyCharm remained bound to an older `.venv` interpreter path after switching from Python 3.13 to Python 3.12. | Reconfigured PyCharm **Settings → Project → Python Interpreter** to `C:\Users\Celine Tan\PycharmProjects\Fortis_CT\.venv\Scripts\python.exe`. | PyCharm indexed all `requirements-dev.txt` packages (`fastapi`, `chromadb`, `pytest`, `httpx`) with zero unresolved imports[cite: 1, 3]. |
| **DEP-05** | Standalone CLI evaluation scripts failed to retrieve built-in framework controls (`NIST_CSF`, `OWASP_TOP10`, `CIS_CONTROLS`). | In normal server startup (`core/main.py`), `init_db()` and `seed_all_frameworks()` run inside FastAPI's `@app.on_event("startup")` hook[cite: 10]; standalone CLI scripts importing `Orchestrator` directly bypassed FastAPI lifecycle events[cite: 6, 10]. | Explicitly invoked `store.init_db()` and `seed_all_frameworks()` at initialization in `evaluation/run_benchmark.py`[cite: 10, 22]. | Verified `security_frameworks` ChromaDB collection seeded and returned framework citations during CLI benchmark runs. |

---

## Stage 1 Validation Evidence

1. **Offline Sanity Test Suite (`pytest`):**
   - Executed via `setup.ps1` Step 5 with `EMBEDDING_BACKEND="hash-stub"`[cite: 2]:
     ```powershell
     $env:EMBEDDING_BACKEND = "hash-stub"
     .\.venv\Scripts\python.exe -m pytest desktop\tests\ -q --no-header
     ```
   - **Result:** All unit and integration tests in `desktop\tests\` passed (`[ok] Self-tests passed`)[cite: 2].

2. **Desktop & Headless Server Verification:**
   - Launched `desktop\app.py` (FastAPI backend on `127.0.0.1:8757` wrapped in native `pywebview`)[cite: 4, 5].
   - Verified `/health` endpoint status and `/api/model/status` hardware/model telemetry[cite: 5, 10].
   - Completed one-time local open-source GGUF weight download via `huggingface_hub` (`settings.models_dir`) and confirmed end-to-end local inference with zero external commercial API calls[cite: 1, 2, 9, 20].

3. **Version Control Hygiene:**
   - Excluded local artifacts (`.venv/`, `.llama-cuda-ok`, downloaded `.gguf` weights, ChromaDB runtime data, `.pytest_cache/`, `.idea/`) via `.gitignore`.
   - Committed patched `setup.ps1` and `TROUBLESHOOTING.md` as atomic, reproducible deployment fixes.