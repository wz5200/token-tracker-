"""Chain clients: BSC (EVM) + Solana + DexScreener market data.
All functions are read-only. Missing data is reported, never fabricated.
"""
import requests

BSC_RPC_DEFAULT = "https://bsc-dataseed.binance.org"
WORLD_RPC_DEFAULT = "https://worldchain.drpc.org"
SOL_RPC_DEFAULT = "https://api.mainnet-beta.solana.com"
DEX_URL = "https://api.dexscreener.com/latest/dex/tokens/{}"
BSCSCAN_API = "https://api.bscscan.com/api"
ETHERSCAN_V2 = "https://api.etherscan.io/v2/api"

# EVM chains: key -> {label, chainid, default rpc}
EVM_CHAINS = {
    "bsc": {"label": "BSC", "chainid": 56, "rpc_default": BSC_RPC_DEFAULT,
            "explorer": "BscScan", "native": "BNB", "gmgn": "bsc"},
    "world": {"label": "World Chain", "chainid": 480, "rpc_default": WORLD_RPC_DEFAULT,
              "explorer": "Worldscan", "native": "ETH", "gmgn": None},
}

BURN_ADDRESSES = [
    "0x000000000000000000000000000000000000dead",
    "0x0000000000000000000000000000000000000000",
]

SEL_TOTAL_SUPPLY = "0x18160ddd"
SEL_DECIMALS = "0x313ce567"
SEL_BALANCE_OF = "0x70a08231"


def _rpc(url, method, params, timeout=25):
    r = requests.post(
        url,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        timeout=timeout,
        headers={"Content-Type": "application/json"},
    )
    r.raise_for_status()
    j = r.json()
    if isinstance(j, dict) and j.get("error"):
        raise RuntimeError(str(j["error"]))
    return j["result"]


# ---------------- EVM (BSC) ----------------

def evm_call(rpc, to_addr, data):
    return _rpc(rpc, "eth_call", [{"to": to_addr, "data": data}, "latest"])


def evm_total_supply(rpc, token):
    return int(evm_call(rpc, token, SEL_TOTAL_SUPPLY), 16)


def evm_decimals(rpc, token):
    return int(evm_call(rpc, token, SEL_DECIMALS), 16)


def evm_balance_of(rpc, token, holder):
    h = holder.lower().replace("0x", "").zfill(64)
    return int(evm_call(rpc, token, SEL_BALANCE_OF + h), 16)


def bscscan(params, api_key, timeout=25):
    p = dict(params)
    p["apikey"] = api_key
    r = requests.get(BSCSCAN_API, params=p, timeout=timeout)
    r.raise_for_status()
    return r.json()


def evm_get_code(rpc, addr):
    return _rpc(rpc, "eth_getCode", [addr, "latest"])


def evm_native_balance(rpc, addr):
    return int(_rpc(rpc, "eth_getBalance", [addr, "latest"]), 16)


def evm_tx_count(rpc, addr):
    return int(_rpc(rpc, "eth_getTransactionCount", [addr, "latest"]), 16)


def evm_scan(chainid, params, api_key, timeout=25):
    """Etherscan V2 unified API (chainid 56=BSC, 480=World Chain).
    One Etherscan-family API key works across chains."""
    p = dict(params)
    p["chainid"] = chainid
    p["apikey"] = api_key
    r = requests.get(ETHERSCAN_V2, params=p, timeout=timeout)
    r.raise_for_status()
    return r.json()


def evm_holders(chainid, contract, api_key, page=1, offset=50):
    """Top token holders (needs free Etherscan-family API key)."""
    j = evm_scan(chainid,
                 {"module": "account", "action": "tokenholderlist",
                  "contractaddress": contract, "page": page, "offset": offset},
                 api_key)
    if j.get("status") != "1":
        return None, j.get("message", "unknown error")
    return j.get("result", []), None


def evm_token_txs(chainid, contract, address, api_key, page=1, offset=200,
                  sort="desc"):
    j = evm_scan(chainid,
                 {"module": "account", "action": "tokentx", "contractaddress": contract,
                  "address": address, "page": page, "offset": offset, "sort": sort},
                 api_key)
    if j.get("status") != "1":
        return None, j.get("message", "unknown error")
    return j.get("result", []), None


def evm_contract_creator(chainid, contract, api_key):
    j = evm_scan(chainid,
                 {"module": "contract", "action": "getcontractcreation",
                  "contractaddresses": contract},
                 api_key)
    if j.get("status") != "1":
        return None, j.get("message", "unknown error")
    res = j.get("result") or []
    if not res:
        return None, "empty"
    return res[0].get("contractCreator"), None


def evm_txlist(chainid, address, api_key, page=1, offset=200, sort="desc"):
    """普通交易列表（查合约创建、资金来源用）"""
    j = evm_scan(chainid,
                 {"module": "account", "action": "txlist", "address": address,
                  "page": page, "offset": offset, "sort": sort},
                 api_key)
    if j.get("status") != "1":
        return None, j.get("message", "unknown error")
    return j.get("result", []), None


def bsc_holders(contract, api_key, page=1, offset=50):
    """Top token holders via BscScan (needs free API key)."""
    return evm_holders(56, contract, api_key, page, offset)


def bsc_token_txs(contract, address, api_key, page=1, offset=200, sort="desc"):
    return evm_token_txs(56, contract, address, api_key, page, offset, sort)


def bsc_contract_creator(contract, api_key):
    return evm_contract_creator(56, contract, api_key)


# ---------------- Solana ----------------

def sol_rpc(rpc, method, params, timeout=25):
    return _rpc(rpc, method, params, timeout)


def sol_largest_accounts(rpc, mint):
    return sol_rpc(rpc, "getTokenLargestAccounts",
                   [{"mint": mint}, {"commitment": "confirmed"}])["value"]


def sol_token_supply(rpc, mint):
    return sol_rpc(rpc, "getTokenSupply", [mint, {"commitment": "confirmed"}])["value"]


def sol_signatures(rpc, address, limit=200, before=None):
    params = [{"limit": limit, "commitment": "confirmed"}]
    if before:
        params[0]["before"] = before
    return sol_rpc(rpc, "getSignaturesForAddress", [address] + params)


def sol_token_accounts_by_owner(rpc, owner, mint):
    return sol_rpc(
        rpc, "getTokenAccountsByOwner",
        [owner,
         {"mint": mint},
         {"encoding": "jsonParsed", "commitment": "confirmed"}])["value"]


# ---------------- DexScreener (market data, keyless) ----------------

def dex_info(address):
    """Returns best-liquidity pair info or None."""
    try:
        r = requests.get(DEX_URL.format(address), timeout=20)
        r.raise_for_status()
        pairs = (r.json().get("pairs") or [])
    except Exception:
        return None
    if not pairs:
        return None

    def liq(p):
        try:
            return float((p.get("liquidity") or {}).get("usd") or 0)
        except Exception:
            return 0

    p = max(pairs, key=liq)
    txns = p.get("txns") or {}
    h24 = txns.get("h24") or {}
    h1 = txns.get("h1") or {}
    vol = p.get("volume") or {}
    chg = p.get("priceChange") or {}
    base = p.get("baseToken") or {}
    return {
        "pair_address": p.get("pairAddress"),
        "dex": p.get("dexId"),
        "chain": p.get("chainId"),
        "name": base.get("name"),
        "symbol": base.get("symbol"),
        "price_usd": _f(p.get("priceUsd")),
        "fdv": _f(p.get("fdv")),
        "liquidity_usd": _f((p.get("liquidity") or {}).get("usd")),
        "volume_h24": _f(vol.get("h24")),
        "buys_h24": h24.get("buys"),
        "sells_h24": h24.get("sells"),
        "buys_h1": h1.get("buys"),
        "sells_h1": h1.get("sells"),
        "chg_h1": _f(chg.get("h1")),
        "chg_h24": _f(chg.get("h24")),
        "url": p.get("url"),
    }


def _f(v):
    try:
        return float(v) if v is not None else None
    except Exception:
        return None
