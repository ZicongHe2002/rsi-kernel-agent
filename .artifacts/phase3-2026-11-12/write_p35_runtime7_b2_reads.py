"""Write the first B2 Analyzer read response and preserve its packet bytes."""
import json
from pathlib import Path

D = Path(__file__).resolve().parent
TARGET = D / "continuation-e4b6a0fcf34a6c37d083"
B = "mla-strategy:ad3416851dfcc72142373541d6dc1f47121b390cce460b7fa479e9436b97e3cd"
METHODS = (
    "hist-decode-dma2", "hist-decode-prepare-padding", "hist-fwd-state-lifetime",
    "hist-profile-accounting", "official-jax-synchronized-benchmark",
    "official-tpu-profile-mode-version-gates",
)


def main():
    packet_path = TARGET / "packet.json"
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    assert packet["protocol"] == "rsi-analyzer-session.v1"
    assert packet["manifest_digest"] == json.loads((TARGET / "session.json").read_text(encoding="utf-8"))["manifest"]["digest"]
    row = packet["manifest"]["strategies"][B]
    assert row["context"]["environment"]["runtime_session_id"] == "tpu-v6e1-s-euw4a0-3qldb90w629mv"
    assert row["parent_id"] == "wf-parent:88fc2af19b8d773ad8ce15664ff31573f958e8a3"
    profile_id = row["profiles"][0]["id"]
    response = {"tool_calls": [
        {"id": "B-source", "name": "source", "arguments": {"strategy_id": B}},
        {"id": "B-history", "name": "history", "arguments": {"strategy_id": B}},
        {"id": "B-profile-0", "name": "profile", "arguments": {"profile_id": profile_id, "strategy_id": B}},
        *({"id": "read-" + method, "name": "knowledge", "arguments": {"method_id": method}} for method in METHODS),
    ]}
    (TARGET / "packet-after-reads.json").write_bytes(packet_path.read_bytes())
    (TARGET / "response-1-read-inputs.json").write_text(json.dumps(response, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(json.dumps({"manifest_digest": packet["manifest_digest"], "profile_id": profile_id,
                      "reads": [c["id"] for c in response["tool_calls"]]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
