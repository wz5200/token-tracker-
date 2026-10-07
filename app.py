"""Token health-check web app (self-hosted).
Chains: BSC, World Chain (EVM) + Solana. Read-only: never trades, never connects a wallet.
"""
import os
import re
from flask import Flask, request, jsonify, send_from_directory

import analyzer
import chains

app = Flask(__name__, static_folder="static", static_url_path="/static")

BSC_RPC = os.environ.get("BSC_RPC_URL", chains.BSC_RPC_DEFAULT)
WORLD_RPC = os.environ.get("WORLDCHAIN_RPC_URL", chains.WORLD_RPC_DEFAULT)
SOL_RPC = os.environ.get("SOLANA_RPC_URL", chains.SOL_RPC_DEFAULT)
# One Etherscan-family key covers BSC (BscScan) and World Chain (Worldscan) via V2 API.
SCAN_KEY = (os.environ.get("ETHERSCAN_API_KEY")
            or os.environ.get("BSCSCAN_API_KEY", ""))


def detect_kind(addr):
    addr = (addr or "").strip()
    if re.fullmatch(r"0x[0-9a-fA-F]{40}", addr):
        return "evm"
    if re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}", addr):
        return "sol"
    return None


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/analyze")
def api_analyze():
    contract = (request.args.get("contract") or "").strip()
    if not contract:
        return jsonify({"ok": False, "error": "missing contract"}), 400
    kind = detect_kind(contract)
    if not kind:
        return jsonify({"ok": False,
                        "error": "unrecognized address format"}), 400
    try:
        if kind == "sol":
            report = analyzer.analyze_sol(contract, SOL_RPC)
        else:
            chain = (request.args.get("chain") or "bsc").lower()
            if chain not in chains.EVM_CHAINS:
                return jsonify({"ok": False,
                                "error": "unknown EVM chain"}), 400
            rpc = WORLD_RPC if chain == "world" else BSC_RPC
            report = analyzer.analyze_evm(contract, rpc, chain, SCAN_KEY)
        return jsonify({"ok": True, "report": report})
    except Exception as e:
        return jsonify({"ok": False,
                        "error": f"analysis failed: {str(e)[:200]}"}), 500


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "scan_key": bool(SCAN_KEY),
                    "chains": list(chains.EVM_CHAINS) + ["sol"]})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
