"""Recreate the read-result packet locally without touching the Store."""
from dataclasses import asdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src")]
from rsi_kernel_agent.contracts import ArtifactRef
from rsi_kernel_agent.profiler.analyzer import Analyzer
from rsi_kernel_agent.profiler.inputs import load_manifest
from rsi_kernel_agent.store import ExperimentStore

D = Path(__file__).resolve().parent
TARGET = D / "continuation-e4b6a0fcf34a6c37d083"


def main():
    session = json.loads((TARGET / "session.json").read_text(encoding="utf-8"))
    packet = json.loads((TARGET / "packet.json").read_text(encoding="utf-8"))
    response = json.loads((TARGET / "response-1-read-inputs.json").read_text(encoding="utf-8"))
    store = ExperimentStore(D / "experiments.sqlite3", D / "artifacts")
    manifest = load_manifest(store, ArtifactRef(**session["manifest"]))
    analyzer = Analyzer(manifest, artifacts=store.artifacts, **session["options"])
    analyzer.advance(response)
    result = analyzer.packet()
    if result.get("manifest_digest") != packet.get("manifest_digest"):
        raise RuntimeError("Replay manifest digest differs")
    (TARGET / "packet-after-reads.json").write_text(
        json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(json.dumps({"manifest_digest": result["manifest_digest"],
                      "tool_results": len(result["tool_results"]),
                      "calculations": len(result["calculations"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
