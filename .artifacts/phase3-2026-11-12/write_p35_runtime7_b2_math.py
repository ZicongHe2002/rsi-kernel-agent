"""Write the bounded B2 logical-payload calculation response."""
import json
from pathlib import Path

D = Path(__file__).resolve().parent
TARGET = D / "continuation-e4b6a0fcf34a6c37d083"
B = "mla-strategy:ad3416851dfcc72142373541d6dc1f47121b390cce460b7fa479e9436b97e3cd"


def calc(cid, buffers, assumption):
    return {"id": cid, "name": "calculate", "arguments": {
        "model": "live_memory", "inputs": {"buffers": [
            {"name": n, "elements": e, "dtype_bytes": w, "copies": 1, "start": 0, "end": 1}
            for n, e, w in buffers]}, "evidence_refs": ["source:" + B],
        "assumptions": [
            "Runtime7 B2 small B1/H16/N1024/Dpad640 logical interface only; not physical VMEM, DMA, or measured traffic.",
            "Compiler folding, hidden temporaries, synchronization, and layout conversions are excluded; no runtime speedup is predicted.",
            assumption,
        ]}}


def main():
    response = {"tool_calls": [
        calc("R2-B-query-prepared", [("padded_query", 16 * 640, 2)], "The parent prepare output includes padded query."),
        calc("R2-B-query-raw", [("raw_query", 16 * 576, 2)], "The candidate passes raw query and performs padding in attention."),
        calc("R2-B-prepare-interface", [("padded_query", 16 * 640, 2), ("broadcast_new", 1024 * 640, 2), ("lengths", 16 * 1024, 4)], "B1 fusion removes separate prepare outputs from the interface; equivalent values may remain compiler temporaries."),
        calc("R2-B-raw-interface", [("raw_query", 16 * 576, 2), ("raw_new", 576, 2), ("raw_lengths", 1, 4)], "Only static batch one uses raw q/new/lengths; full cache input/output remains unchanged and is excluded."),
    ]}
    (TARGET / "response-2-math.json").write_text(json.dumps(response, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(json.dumps({"calls": [c["id"] for c in response["tool_calls"]]}))


if __name__ == "__main__":
    main()
