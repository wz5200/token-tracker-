"""Full token health-check engine: concentration, insider wallets, LP lock,
creator profile, big moves, wash-trade detection, and a 0-100 risk score.
Every module returns data + a one-line plain-language conclusion (zh/en).
Data that cannot be obtained is marked missing, never invented.
"""
from collections import defaultdict
import chains

# ---------------- helpers ----------------

def mod(key, title_zh, title_en, status, data, zh, en):
    return {"key": key, "title_zh": title_zh, "title_en": title_en,
            "status": status, "data": data,
            "conclusion_zh": zh, "conclusion_en": en}


def pct(x):
    return round(x * 100, 2) if x is not None else None


def gini(values):
    if not values:
        return None
    v = sorted(values)
    n = len(v)
    s = sum(v)
    if s == 0:
        return 0.0
    cum = sum((i + 1) * x for i, x in enumerate(v))
    return round((2 * cum) / (n * s) - (n + 1) / n, 4)


# ---------------- EVM analysis (BSC / World Chain) ----------------

def analyze_evm(contract, rpc_url, chain_key, api_key):
    cfg = chains.EVM_CHAINS[chain_key]
    chainid = cfg["chainid"]
    label = cfg["label"]
    explorer = cfg["explorer"]
    key_hint = (f"需要免费的 {explorer} API Key（Etherscan 系 Key 通用，.env 里填 ETHERSCAN_API_KEY）。"
                if chain_key == "world" else
                "需要免费的 BscScan API Key 才能读大户榜（.env 里填 BSCSCAN_API_KEY 或 ETHERSCAN_API_KEY）。")
    contract = contract.strip()
    modules = []
    ctx = {"chain": label, "contract": contract}

    # ---- overview (DexScreener) ----
    dex = chains.dex_info(contract)
    if dex:
        ctx.update(dex)
        modules.append(mod(
            "overview", "行情概览", "Market Overview", "ok", dex,
            f"{dex['symbol'] or '该币'}现价 ${dex['price_usd'] or '未知'}，FDV ${fmt_money(dex['fdv'])}，"
            f"流动性 ${fmt_money(dex['liquidity_usd'])}，24h成交 ${fmt_money(dex['volume_h24'])}。",
            f"{dex['symbol'] or 'This token'} trades at ${dex['price_usd'] or 'unknown'}, "
            f"FDV ${fmt_money(dex['fdv'])}, liquidity ${fmt_money(dex['liquidity_usd'])}, "
            f"24h volume ${fmt_money(dex['volume_h24'])}.",
        ))
    else:
        modules.append(mod(
            "overview", "行情概览", "Market Overview", "missing", {},
            "DexScreener 没有该合约的行情数据，可能是新币或已下架交易对。",
            "No market data on DexScreener for this contract; it may be new or delisted.",
        ))

    # ---- supply ----
    supply = None
    try:
        raw = chains.evm_total_supply(rpc_url, contract)
        dec = chains.evm_decimals(rpc_url, contract)
        supply = raw / (10 ** dec)
        ctx["total_supply"] = supply
        ctx["decimals"] = dec
        modules.append(mod(
            "supply", "总供应量", "Total Supply", "ok",
            {"total_supply": supply, "decimals": dec},
            f"总供应量 {fmt_num(supply)} 枚。",
            f"Total supply is {fmt_num(supply)} tokens.",
        ))
    except Exception as e:
        modules.append(mod(
            "supply", "总供应量", "Total Supply", "missing", {"error": str(e)[:120]},
            "链上读不到总供应量，RPC 可能暂时不可用。",
            "Total supply could not be read on-chain; the RPC may be unavailable.",
        ))

    # ---- LP burn / lock ----
    lp_mod = {"burned_pct": None, "pool": (dex or {}).get("pair_address")}
    lp_status, lp_zh, lp_en = "missing", "", ""
    try:
        pool = (dex or {}).get("pair_address")
        if pool and supply:
            lp_total = chains.evm_total_supply(rpc_url, pool)
            burned = sum(chains.evm_balance_of(rpc_url, pool, a)
                         for a in chains.BURN_ADDRESSES)
            if lp_total > 0:
                burned_pct = burned / lp_total * 100
                lp_mod.update({"burned_pct": round(burned_pct, 2),
                               "lp_total": lp_total, "lp_burned": burned})
                lp_status = "ok"
                if burned_pct >= 99:
                    lp_zh = (f"LP 约 {burned_pct:.1f}% 已打入黑洞销毁，池子基本跑不掉（按当前池口径）。"
                             "注意：项目方仍可能通过增发或换池跑路，销毁不等于零风险。")
                    lp_en = (f"About {burned_pct:.1f}% of LP is burned to the dead address; "
                             "the pool itself is effectively unruggable (for this pool). "
                             "Note: minting or pool migration can still rug; burned LP is not zero risk.")
                elif burned_pct >= 50:
                    lp_zh = f"LP 销毁 {burned_pct:.1f}%，剩余部分需确认是否锁仓及解锁时间。"
                    lp_en = (f"{burned_pct:.1f}% of LP is burned; "
                             "confirm whether the rest is locked and when it unlocks.")
                else:
                    lp_zh = (f"LP 仅销毁 {burned_pct:.1f}%，大头还在外面，跑路风险高，"
                             "务必查锁仓合约与解锁时间。")
                    lp_en = (f"Only {burned_pct:.1f}% of LP is burned; most of it is still out there. "
                             "High rug risk — check the lock contract and unlock time.")
            else:
                lp_status, lp_zh, lp_en = "partial", "读到 LP 总量为 0，数据异常。", "LP total reads as 0; data looks abnormal."
        else:
            lp_zh = "找不到交易池地址，无法计算 LP 销毁比例。"
            lp_en = "No pool address found; LP burn ratio cannot be computed."
    except Exception as e:
        errmsg = str(e)[:80]
        if "reverted" in errmsg or "revert" in errmsg:
            lp_zh = ("该池可能是 Uniswap V3 类型（仓位为 NFT，没有传统 LP 代币），"
                     "LP 销毁口径不适用；要确认锁仓请查 V3 头寸 NFT。")
            lp_en = ("This looks like a Uniswap V3 pool (positions are NFTs, no LP token); "
                     "the LP-burn check does not apply. Check V3 position NFTs for locks.")
        else:
            lp_zh = f"LP 查询失败（{errmsg}），本项数据不足。"
            lp_en = f"LP lookup failed ({errmsg}); data insufficient."
    modules.append(mod("lp", "LP 锁仓/销毁", "LP Lock / Burn", lp_status, lp_mod, lp_zh, lp_en))

    # ---- holders (needs BscScan key) ----
    holders = []
    if api_key:
        rows, err = chains.evm_holders(chainid, contract, api_key, offset=50)
        if rows:
            for r in rows:
                try:
                    holders.append({"address": r["TokenHolderAddress"],
                                    "balance": float(r["TokenHolderQuantity"])})
                except Exception:
                    continue
    if holders and supply:
        bals = [h["balance"] for h in holders]
        total_bal = sum(bals)
        # normalize in case holder list covers less than supply
        top10 = sum(sorted(bals, reverse=True)[:10]) / supply
        whales = sum(1 for b in bals if b / supply >= 0.01)
        g = gini(bals)
        ctx["holders"] = holders
        data = {"count_sampled": len(holders), "top10_pct": pct(top10),
                "whales_ge_1pct": whales, "gini": g,
                "top": [{"address": h["address"],
                         "pct": pct(h["balance"] / supply)} for h in
                        sorted(holders, key=lambda x: -x["balance"])[:10]]}
        if top10 >= 0.5:
            zh = (f"前十大户吃掉 {top10*100:.1f}%，{whales} 个地址持仓超 1%，筹码高度集中，"
                  "几个大户砸盘就能腰斩。")
            en = (f"Top 10 holders own {top10*100:.1f}%, {whales} addresses hold over 1%. "
                  "Highly concentrated — a few whales dumping can halve the price.")
        elif top10 >= 0.3:
            zh = f"前十大户占 {top10*100:.1f}%，集中度偏高，大户动向值得盯。"
            en = (f"Top 10 hold {top10*100:.1f}% — fairly concentrated, "
                  "watch whale moves.")
        else:
            zh = f"前十大户占 {top10*100:.1f}%，筹码相对分散。"
            en = f"Top 10 hold {top10*100:.1f}% — relatively distributed."
        modules.append(mod("holders", "持仓集中度", "Holder Concentration",
                           "ok" if len(holders) >= 20 else "partial",
                           data, zh, en))
    else:
        reason = (key_hint if not api_key else "大户榜读取失败，本项数据不足。")
        modules.append(mod(
            "holders", "持仓集中度", "Holder Concentration", "missing", {},
            reason,
            ("A free Etherscan-family API key is required for the holder list "
             "(set ETHERSCAN_API_KEY in .env).") if not api_key
            else "Holder list lookup failed; data insufficient.",
        ))

    # ---- creator trace ----
    creator = None
    if api_key:
        creator, _ = chains.evm_contract_creator(chainid, contract, api_key)
    if creator:
        ctx["creator"] = creator
        dev_hist = {"coins": None, "note": "7天发币监控需单独任务"}
        modules.append(mod(
            "creator", "创建者追踪", "Creator Trace", "ok",
            {"creator": creator, "history": dev_hist},
            f"创建者钱包：{creator}。建议把该地址加入观察：看它是否还在持仓、是否批量发币。",
            f"Creator wallet: {creator}. Watch this address for remaining holdings "
            f"and batch deployments.",
        ))
    else:
        modules.append(mod(
            "creator", "创建者追踪", "Creator Trace", "missing", {},
            f"查不到创建者地址（需要 {explorer} Key），本项数据不足。",
            "Creator address unavailable (explorer API key required); data insufficient.",
        ))

    # ---- creator deep profile: 批量发币检测 + 资金来源 ----
    if creator:
        native = cfg.get("native", "BNB")
        deep = {"creator": creator}
        try:
            code = chains.evm_get_code(rpc_url, creator)
            deep["is_eoa"] = code in ("0x", "0x0", "")
        except Exception:
            deep["is_eoa"] = None
        try:
            bal = chains.evm_native_balance(rpc_url, creator)
            deep["native_balance"] = round(bal / 1e18, 4)
        except Exception:
            deep["native_balance"] = None
        try:
            deep["tx_total"] = chains.evm_tx_count(rpc_url, creator)
        except Exception:
            deep["tx_total"] = None
        creations = []
        funder = None
        if api_key:
            txs, _ = chains.evm_txlist(chainid, creator, api_key,
                                      offset=200, sort="desc")
            if txs:
                for t in txs:
                    if not t.get("to") and t.get("contractAddress"):
                        creations.append({"contract": t["contractAddress"],
                                          "hash": t.get("hash"),
                                          "time": t.get("timeStamp")})
            txs_asc, _ = chains.evm_txlist(chainid, creator, api_key,
                                           offset=1, sort="asc")
            if txs_asc:
                first = txs_asc[0]
                if first.get("from", "").lower() != creator.lower():
                    funder = first.get("from")
                deep["first_tx_hash"] = first.get("hash")
        deep["creations"] = creations[:10]
        deep["creations_count"] = len(creations)
        deep["funder"] = funder
        n = len(creations)
        if n >= 5:
            dzh = (f"该地址直接创建过 {n}+ 个合约（最近200笔内），批量发币机器实锤，"
                   "跑路风险极高。")
            den = (f"{n}+ direct contract creations in recent 200 txs — "
                   "batch-deployer bot confirmed, extreme rug risk.")
            dst = "ok"
        elif n >= 1:
            dzh = f"该地址直接创建过 {n} 个合约，是发币老手，多看它历史币的下场。"
            den = (f"{n} direct contract creations — experienced deployer, "
                   "check what happened to its past tokens.")
            dst = "ok"
        elif api_key:
            dzh = ("近200笔内无直接合约创建记录。如经发币工厂发币，这里看不到，"
                   "结合它的持仓币种和交易频率判断。")
            den = ("No direct creations in recent 200 txs. Factory-deployed tokens "
                   "would not show here; judge by its holdings and activity.")
            dst = "partial"
        else:
            dzh = f"需要 {explorer} Key 才能查它的发币记录和资金来源，本项数据不足。"
            den = f"{explorer} API key required for deploy history and funding source."
            dst = "missing"
        if funder:
            dzh += f"第一笔钱来自 {funder[:10]}…，可顺藤摸瓜查上游分发钱包。"
            den += f" First funded by {funder[:10]}… — trace upstream for distributor wallets."
        tinfo = f"{deep['native_balance']} {native}" if deep.get("native_balance") is not None else "未知"
        modules.append(mod("creator_deep", "创建者深度画像", "Creator Deep Profile",
                           dst, deep,
                           f"创建者余额 {tinfo}，累计交易 {deep.get('tx_total') or '未知'} 笔。" + dzh,
                           f"Creator holds {tinfo}, {deep.get('tx_total') or 'unknown'} txs. " + den))
    else:
        modules.append(mod("creator_deep", "创建者深度画像", "Creator Deep Profile",
                           "missing", {},
                           "创建者地址未知，无法画像。",
                           "Creator unknown; profiling unavailable."))

    # ---- promo leads: 宣发线索一键搜 ----
    promo_links = {
        "x_search": f"https://x.com/search?q={contract}&f=live",
        "google": f"https://www.google.com/search?q=%22{contract}%22",
    }
    if cfg.get("gmgn"):
        promo_links["gmgn"] = (f"https://gmgn.ai/{cfg['gmgn']}/token/{contract}")
    modules.append(mod(
        "promo", "宣发线索", "Promo Leads", "ok",
        {"links": promo_links},
        "点上面的链接搜这个合约地址：看有没有官推/电报群/官网。只有一堆喊单号、找不到官号=高危信号；官号粉丝少、刚注册=更要小心。",
        "Search the contract address via the links above for official Twitter/Telegram/website. "
        "Only shill accounts and no official presence = red flag.",
    ))
    sources = []
    if api_key and ctx.get("holders"):
        pool = ((dex or {}).get("pair_address") or "").lower()
        top = sorted(ctx["holders"], key=lambda x: -x["balance"])[:8]
        for h in top:
            txs, err = chains.evm_token_txs(chainid, contract, h["address"], api_key,
                                           offset=100, sort="desc")
            if not txs:
                sources.append({"holder": h["address"], "source": None,
                                "note": "no tx data"})
                continue
            incoming = [t for t in txs if t.get("to", "").lower() == h["address"].lower()]
            if not incoming:
                sources.append({"holder": h["address"], "source": None,
                                "note": "no incoming"})
                continue
            # largest single incoming transfer
            try:
                big = max(incoming,
                          key=lambda t: float(t.get("value", 0)) / (10 ** int(t.get("tokenDecimal", 18))))
                src = big.get("from", "")
                sources.append({
                    "holder": h["address"],
                    "holder_pct": pct(h["balance"] / supply) if supply else None,
                    "source": src,
                    "source_is_pool": src.lower() == pool,
                    "amount": big.get("value"),
                    "tx_hash": big.get("hash"),
                })
            except Exception:
                sources.append({"holder": h["address"], "source": None,
                                "note": "parse error"})
        # cluster: sources funding multiple holders
        by_src = defaultdict(list)
        for s in sources:
            if s.get("source"):
                by_src[s["source"].lower()].append(s["holder"])
        clusters = [{"source": k, "holders": v, "count": len(v)}
                    for k, v in by_src.items() if len(v) >= 2]
        ctx["source_clusters"] = clusters
        if clusters:
            big = max(clusters, key=lambda c: c["count"])
            zh = (f"{len(clusters)} 组大户的币来自同一个源头，其中 {big['source'][:10]}… "
                  f"喂了 {big['count']} 个大户，疑似庄家分仓或捆绑地址。")
            en = (f"{len(clusters)} cluster(s) of whales share one funding source; "
                  f"{big['source'][:10]}… funded {big['count']} whales — "
                  f"likely team-distributed or bundled wallets.")
        else:
            zh = "大户们的币来源各不相同，没发现明显的同一源头分仓。"
            en = "Top holders have distinct funding sources; no obvious shared-source distribution."
        modules.append(mod("sources", "资金源头追踪", "Fund Source Trace",
                           "ok", {"traces": sources, "clusters": clusters}, zh, en))
    else:
        modules.append(mod(
            "sources", "资金源头追踪", "Fund Source Trace", "missing", {},
            f"需要 {explorer} Key 才能追踪大户资金来源，本项数据不足。",
            "Explorer API key required for fund-source tracing; data insufficient.",
        ))

    # ---- insider / sniper / bundle ----
    insider = {"snipers": [], "bundles": ctx.get("source_clusters", []),
               "creator_linked": []}
    izh = ien = ""
    if api_key:
        txs, err = chains.evm_token_txs(chainid, contract, contract, api_key,
                                       offset=100, sort="asc")
        # earliest transfers: buyers right after launch (exclude pool & zero addr)
        if txs:
            seen = []
            for t in txs[:60]:
                to = t.get("to", "")
                frm = t.get("from", "")
                if to and frm and "0000000000000000000000000000000000000000" not in to:
                    if to.lower() != ((dex or {}).get("pair_address") or "").lower():
                        seen.append(to)
                        if len(seen) >= 10:
                            break
            insider["snipers"] = seen
    nb = len(insider["bundles"])
    ns = len(insider["snipers"])
    if ns or nb:
        izh = (f"发现 {ns} 个开盘极早期买入地址（疑似狙击手），{nb} 组捆绑/分仓地址。"
               "老鼠仓嫌疑不小：早鸟+分仓往往意味着开盘即有人埋伏。")
        ien = (f"Found {ns} very-early buyers (possible snipers) and {nb} bundled/distribution "
               "clusters. Insider suspicion is real: early birds plus split wallets "
               "often mean someone was positioned before launch.")
        st = "ok"
    elif not api_key:
        izh = f"需要 {explorer} Key 才能做老鼠仓检测，本项数据不足。"
        ien = "Explorer API key required for insider detection; data insufficient."
        st = "missing"
    else:
        izh = "没发现明显的狙击手或捆绑地址，老鼠仓迹象不明显。"
        ien = "No obvious snipers or bundled wallets; insider signs are weak."
        st = "ok"
    modules.append(mod("insider", "老鼠仓检测", "Insider Detection", st, insider, izh, ien))

    # ---- big moves (large transfers vs pool) ----
    bigmoves = []
    if api_key:
        txs, err = chains.evm_token_txs(chainid, contract, contract, api_key,
                                       offset=100, sort="desc")
        if txs and supply:
            pool = ((dex or {}).get("pair_address") or "").lower()
            for t in txs[:100]:
                try:
                    amt = float(t.get("value", 0)) / (10 ** int(t.get("tokenDecimal", 18)))
                except Exception:
                    continue
                if supply and amt / supply >= 0.005:  # >=0.5% of supply
                    frm, to = t.get("from", ""), t.get("to", "")
                    side = ("卖出砸盘" if to.lower() == pool else
                            "买入" if frm.lower() == pool else "大额转账")
                    bigmoves.append({"hash": t.get("hash"), "from": frm, "to": to,
                                     "pct": pct(amt / supply), "side": side,
                                     "side_en": "dump into pool" if to.lower() == pool else
                                     "buy" if frm.lower() == pool else "large transfer"})
                    if len(bigmoves) >= 10:
                        break
    if bigmoves:
        dumps = sum(1 for b in bigmoves if "砸盘" in b["side"])
        zh = (f"近期 {len(bigmoves)} 笔超 0.5% 供应量的大额异动，其中 {dumps} 笔是砸进池子的卖出。"
              + ("大户正在出货，谨慎。" if dumps >= 3 else "大额异动存在，多看两眼。"))
        en = (f"{len(bigmoves)} recent moves over 0.5% of supply, {dumps} of them dumps into the pool. "
              + ("Whales appear to be distributing — be careful." if dumps >= 3
                 else "Large moves exist; keep an eye on them."))
        st = "ok"
    elif not api_key:
        zh, en, st = (f"需要 {explorer} Key，本项数据不足。",
                      "Explorer API key required; data insufficient.", "missing")
    else:
        zh, en, st = ("近期没有超 0.5% 供应量的大额异动，盘面相对安静。",
                      "No recent moves over 0.5% of supply; relatively quiet.", "ok")
    modules.append(mod("bigmoves", "大额异动", "Large Moves", st,
                       {"moves": bigmoves}, zh, en))

    # ---- wash trade ----
    wash = {"buys_h24": (dex or {}).get("buys_h24"),
            "sells_h24": (dex or {}).get("sells_h24")}
    b, s = wash["buys_h24"], wash["sells_h24"]
    if b and s and dex:
        ratio = min(b, s) / max(b, s)
        if 0.9 <= ratio <= 1.0 and (b + s) > 40:
            zh = (f"24h 买 {b} 笔 / 卖 {s} 笔，数量高度对称，疑似刷量或对倒，"
                  "成交额水分大。")
            en = (f"24h: {b} buys / {s} sells — highly symmetric counts suggest wash trading; "
                  "volume may be inflated.")
        elif b > s * 2:
            zh = f"24h 买 {b} 笔远多于卖 {s} 笔，买盘占优。"
            en = f"24h: {b} buys vs {s} sells — buys dominate."
        elif s > b * 2:
            zh = f"24h 卖 {s} 笔远多于买 {b} 笔，抛压占优，小心。"
            en = f"24h: {s} sells vs {b} buys — sell pressure dominates, be careful."
        else:
            zh = f"24h 买 {b} 笔 / 卖 {s} 笔，买卖相对均衡。"
            en = f"24h: {b} buys / {s} sells — relatively balanced."
        st = "ok"
    else:
        zh, en, st = ("拿不到买卖笔数，无法判断是否刷量。",
                      "Buy/sell counts unavailable; wash check inconclusive.", "missing")
    modules.append(mod("wash", "刷量/对倒检测", "Wash-Trade Check", st, wash, zh, en))

    # ---- risk score ----
    score, factors = risk_score(ctx, dex, modules)
    blind = [m["title_zh"] for m in modules
             if m["key"] in ("holders", "sources", "insider", "creator")
             and m["status"] == "missing"]
    if blind:
        factors.append({"points": 0,
                        "zh": "⚠ 数据盲区：" + "、".join(blind) + "缺失，评分仅基于可验证项",
                        "en": "⚠ Blind spots: " + ", ".join(
                            m["title_en"] for m in modules
                            if m["key"] in ("holders", "sources", "insider", "creator")
                            and m["status"] == "missing") + " missing; score covers verifiable items only"})
    suffix_zh = "（部分数据缺失，评分不完整）" if blind else ""
    suffix_en = " (incomplete data)" if blind else ""
    if score >= 70:
        vzh = f"综合风险 {score} 分{suffix_zh}：高危，快跑，别碰。"
        ven = f"Risk score {score}{suffix_en}: DANGER — stay away."
    elif score >= 40:
        vzh = f"综合风险 {score} 分{suffix_zh}：中等风险，谨慎，仓位要轻、设好止损。"
        ven = f"Risk score {score}{suffix_en}: medium risk — be cautious, keep size small, use stops."
    else:
        vzh = (f"综合风险 {score} 分{suffix_zh}：可验证的指标相对健康，但 meme 币随时可能变脸，"
               "只用亏得起的钱。")
        ven = (f"Risk score {score}{suffix_en}: verifiable metrics look relatively healthy, "
               "but meme coins can turn fast — only risk what you can afford to lose.")
    modules.append(mod("score", "综合风险评分", "Risk Score", "ok",
                       {"score": score, "factors": factors}, vzh, ven))

    # ---- AI 综合研判（程序按规则生成，仅供参考，不构成投资建议） ----
    ai = ai_verdict(dex, modules, score)
    modules.append(mod("ai", "AI 综合研判", "AI Verdict",
                       ai["status"], ai["data"], ai["zh"], ai["en"]))

    return {"chain": label, "contract": contract, "market": dex,
            "modules": modules}


def risk_score(ctx, dex, modules):
    score, factors = 0, []

    def add(pts, zh, en):
        nonlocal score
        score += pts
        factors.append({"points": pts, "zh": zh, "en": en})

    def get(key):
        for m in modules:
            if m["key"] == key:
                return m
        return None

    h = get("holders")
    if h and h["status"] in ("ok", "partial"):
        t10 = h["data"].get("top10_pct")
        if t10 is not None:
            if t10 >= 50:
                add(20, f"前十持仓 {t10}%：高度集中", f"Top10 hold {t10}%: highly concentrated")
            elif t10 >= 30:
                add(12, f"前十持仓 {t10}%：偏集中", f"Top10 hold {t10}%: concentrated")
            elif t10 >= 20:
                add(6, f"前十持仓 {t10}%", f"Top10 hold {t10}%")
        g = h["data"].get("gini")
        if g is not None and g >= 0.9:
            add(8, f"Gini {g}：极端不均", f"Gini {g}: extremely unequal")

    ins = get("insider")
    if ins and ins["status"] == "ok":
        if len(ins["data"].get("snipers", [])) >= 3:
            add(8, "多个狙击手地址", "Multiple sniper wallets")
        if ins["data"].get("bundles"):
            add(12, "发现捆绑/分仓地址组", "Bundled/distribution clusters found")

    lp = get("lp")
    if lp and lp["status"] == "ok":
        bp = lp["data"].get("burned_pct")
        if bp is not None:
            if bp < 50:
                add(15, f"LP 仅销毁 {bp}%", f"Only {bp}% of LP burned")
            elif bp < 90:
                add(8, f"LP 销毁 {bp}%，未全锁", f"{bp}% LP burned, not fully locked")

    if dex:
        liq = dex.get("liquidity_usd")
        if liq is not None and liq < 20000:
            add(8, f"流动性仅 ${fmt_money(liq)}：极浅", f"Liquidity only ${fmt_money(liq)}: very thin")
        fdv = dex.get("fdv")
        if fdv and liq and liq > 0 and fdv / liq > 20:
            add(6, "FDV/流动性 > 20：稍有抛压就崩", "FDV/liquidity > 20: fragile")

    w = get("wash")
    if w and w["status"] == "ok":
        b, s = w["data"].get("buys_h24"), w["data"].get("sells_h24")
        if b and s and (b + s) > 40:
            r = min(b, s) / max(b, s)
            if 0.9 <= r <= 1.0:
                add(6, "疑似刷量", "Suspected wash trading")

    bm = get("bigmoves")
    if bm and bm["status"] == "ok":
        dumps = sum(1 for x in bm["data"].get("moves", []) if "砸盘" in x.get("side", ""))
        if dumps >= 3:
            add(8, f"{dumps} 笔大额砸盘", f"{dumps} large dumps")

    return min(score, 100), factors


# ---------------- AI verdict ----------------

def ai_verdict(dex, modules, score):
    """程序按规则生成的综合研判：能不能碰、价格参考、操作纪律。
    仅供参考，不构成投资建议；价格参考由链上/行情数据推算，非预测。"""
    price = (dex or {}).get("price_usd")
    liq = (dex or {}).get("liquidity_usd")
    chg24 = (dex or {}).get("chg_h24")
    data = {}

    score_mod = next((m for m in modules if m["key"] == "score"), {})
    sc = (score_mod.get("data") or {}).get("score")
    if sc is None:
        stance_zh, stance_en, status = "无法研判", "Unscorable", "missing"
    elif sc >= 70:
        stance_zh, stance_en, status = "别碰", "Stay away", "ok"
    elif sc >= 40:
        stance_zh, stance_en, status = "观望为主", "Wait and watch", "ok"
    else:
        stance_zh, stance_en, status = "可小仓位参与", "Small size OK", "ok"
    data["stance_zh"] = stance_zh
    data["stance_en"] = stance_en

    # 理由：取扣分最高的3条
    factors = (score_mod.get("data") or {}).get("factors") or []
    top = sorted([f for f in factors if f.get("points", 0) > 0],
                 key=lambda f: -f["points"])[:3]
    data["reasons_zh"] = [f["zh"] for f in top]
    data["reasons_en"] = [f["en"] for f in top]

    # 价格参考
    data["price"] = price
    p24 = None
    if price and chg24 is not None:
        try:
            p24 = price / (1 + chg24 / 100)
            data["price_24h_ago"] = p24
        except Exception:
            pass
    if liq:
        data["max_single_usd"] = round(liq * 0.02, 2)
    stop = None
    if sc is not None and sc < 70:
        stop = -30 if sc >= 40 else -40
        data["stop_ref_pct"] = stop

    # 结论
    if status == "missing":
        zh = ("AI 研判：无法给出结论——关键数据读不到（行情/大户/供应量缺失），"
              "不做判断比瞎判断强。先解决数据源再看。")
        en = ("AI verdict: no call — key data (market/holders/supply) is missing. "
              "No judgment beats a made-up one. Fix data sources first.")
    else:
        parts_zh = [f"AI 研判：{stance_zh}。"]
        parts_en = [f"AI verdict: {stance_en}."]
        if top:
            parts_zh.append("理由：" + "；".join(f["zh"] for f in top) + "。")
            parts_en.append("Reasons: " + "; ".join(f["en"] for f in top) + ".")
        refs_zh, refs_en = [], []
        if price:
            refs_zh.append(f"现价 ${fmt_price(price)}")
            refs_en.append(f"Now ${fmt_price(price)}")
        if p24:
            refs_zh.append(f"24h前价格约 ${fmt_price(p24)}（回调观察位，非预测）")
            refs_en.append(f"24h-ago price ~${fmt_price(p24)} (pullback watch, not a prediction)")
        if data.get("max_single_usd"):
            refs_zh.append(f"单笔建议不超过 ${fmt_money(data['max_single_usd'])}（流动性2%，控滑点）")
            refs_en.append(f"Keep single buys under ${fmt_money(data['max_single_usd'])} (2% of liquidity)")
        if stop:
            refs_zh.append(f"纪律止损 {stop}%")
            refs_en.append(f"Stop-loss discipline {stop}%")
        if refs_zh:
            parts_zh.append("价格参考：" + "；".join(refs_zh) + "。")
            parts_en.append("Price refs: " + "; ".join(refs_en) + ".")
        parts_zh.append("本研判由程序按规则生成，仅供参考，不构成投资建议；meme币分钟级变脸，决定权在你。")
        parts_en.append("Rule-generated reference only, not investment advice; meme coins turn in minutes — your call.")
        zh, en = "".join(parts_zh), "".join(parts_en)
    return {"status": status, "data": data, "zh": zh, "en": en}


def fmt_price(v):
    if v is None:
        return "未知"
    if v >= 1:
        return f"{v:,.4f}"
    if v >= 0.01:
        return f"{v:.4f}"
    return f"{v:.8f}".rstrip("0").rstrip(".")


# ---------------- Solana analysis ----------------

def analyze_sol(mint, sol_rpc):
    mint = mint.strip()
    modules = []
    dex = chains.dex_info(mint)
    if dex:
        modules.append(mod(
            "overview", "行情概览", "Market Overview", "ok", dex,
            f"{dex['symbol'] or '该币'}现价 ${dex['price_usd'] or '未知'}，FDV ${fmt_money(dex['fdv'])}，"
            f"流动性 ${fmt_money(dex['liquidity_usd'])}，24h成交 ${fmt_money(dex['volume_h24'])}。",
            f"{dex['symbol'] or 'This token'} at ${dex['price_usd'] or 'unknown'}, "
            f"FDV ${fmt_money(dex['fdv'])}, liquidity ${fmt_money(dex['liquidity_usd'])}.",
        ))
    else:
        modules.append(mod(
            "overview", "行情概览", "Market Overview", "missing", {},
            "DexScreener 没有该 mint 的行情数据，可能是新币、已下架或池子已死。",
            "No DexScreener data for this mint; it may be new, delisted, or the pool is dead.",
        ))

    # supply
    supply = None
    try:
        s = chains.sol_token_supply(sol_rpc, mint)
        supply = float(s["amount"]) / (10 ** s["decimals"])
        modules.append(mod("supply", "总供应量", "Total Supply", "ok",
                           {"total_supply": supply},
                           f"总供应量 {fmt_num(supply)} 枚。",
                           f"Total supply {fmt_num(supply)} tokens."))
    except Exception as e:
        modules.append(mod("supply", "总供应量", "Total Supply", "missing",
                           {"error": str(e)[:120]},
                           "读不到供应量，Solana RPC 可能限流或不可用。",
                           "Supply unreadable; Solana RPC may be rate-limited."))

    # holders via getTokenLargestAccounts (keyless)
    holders = []
    try:
        accs = chains.sol_largest_accounts(sol_rpc, mint)
        for a in accs:
            holders.append({"address": a["address"],
                            "balance": float(a["amount"]) / (10 ** a["decimals"])})
    except Exception:
        holders = []
    if holders and supply:
        bals = [h["balance"] for h in holders]
        top10 = sum(sorted(bals, reverse=True)[:10]) / supply
        whales = sum(1 for b in bals if b / supply >= 0.01)
        data = {"count_sampled": len(holders), "top10_pct": pct(top10),
                "whales_ge_1pct": whales, "gini": gini(bals),
                "top": [{"address": h["address"], "pct": pct(h["balance"] / supply)}
                        for h in sorted(holders, key=lambda x: -x["balance"])[:10]]}
        if top10 >= 0.5:
            zh = f"前十大户占 {top10*100:.1f}%（仅采样 {len(holders)} 个最大账户），集中度高。"
            en = (f"Top 10 hold {top10*100:.1f}% (sampled {len(holders)} largest accounts). "
                  "Concentrated.")
        else:
            zh = f"前十大户占 {top10*100:.1f}%，相对分散。"
            en = f"Top 10 hold {top10*100:.1f}% — relatively distributed."
        modules.append(mod("holders", "持仓集中度", "Holder Concentration", "ok",
                           data, zh, en))
    else:
        modules.append(mod("holders", "持仓集中度", "Holder Concentration", "missing",
                           {},
                           "读不到大户数据（Solana RPC 限流或 mint 无效），本项数据不足。",
                           "Holder data unavailable (RPC limited or invalid mint)."))

    # sources / creator / insider: best-effort on Solana, honest about limits
    modules.append(mod("sources", "资金源头追踪", "Fund Source Trace", "partial",
                       {"note": "solana-tx-trace-limited"},
                       "Solana 资金溯源需要逐笔解析交易，本版本只做大户榜，逐笔来源链暂不支持。",
                       "Solana fund tracing needs per-tx parsing; this build covers the holder "
                       "list only."))
    modules.append(mod("creator", "创建者追踪", "Creator Trace", "partial",
                       {"note": "use solscan.io"},
                       "创建者地址建议在 solscan.io 输入 mint 手动查看，本版本暂不自动解析。",
                       "Check the creator manually on solscan.io with the mint address."))
    modules.append(mod("insider", "老鼠仓检测", "Insider Detection", "partial",
                       {"note": "limited"},
                       "Solana 暂只支持大户集中度判断，狙击手/捆绑检测暂不支持。",
                       "Solana currently supports concentration checks only."))

    # big moves / wash via dex
    b, s = (dex or {}).get("buys_h24"), (dex or {}).get("sells_h24")
    if b and s:
        if b > s * 2:
            zh, en = f"24h 买 {b} 笔远多于卖 {s} 笔。", f"24h: {b} buys vs {s} sells."
        elif s > b * 2:
            zh, en = f"24h 卖 {s} 笔远多于买 {b} 笔，抛压大。", f"24h: {s} sells vs {b} buys — heavy selling."
        else:
            zh, en = f"24h 买 {b} / 卖 {s} 笔。", f"24h: {b} buys / {s} sells."
        modules.append(mod("bigmoves", "大额异动", "Large Moves", "partial",
                           {"buys_h24": b, "sells_h24": s}, zh, en))
    else:
        modules.append(mod("bigmoves", "大额异动", "Large Moves", "missing", {},
                           "无成交数据。", "No trade data."))

    score, factors = risk_score({"holders_data": holders}, dex, modules)
    blind = [m["title_zh"] for m in modules
             if m["key"] in ("overview", "holders", "supply")
             and m["status"] == "missing"]
    if blind:
        factors.append({"points": 0,
                        "zh": "⚠ 数据盲区：" + "、".join(blind) + "缺失，评分仅基于可验证项",
                        "en": "⚠ Blind spots: missing " + ", ".join(
                            m["title_en"] for m in modules
                            if m["key"] in ("overview", "holders", "supply")
                            and m["status"] == "missing")})
    if len(blind) >= 2:
        vzh = ("综合风险：无法评分——行情、大户、供应量等关键数据都读不到，"
               "Solana 公开 RPC 可能限流，换节点或稍后重试。别凭感觉下单。")
        ven = ("Risk: unscorable — market, holder and supply data are all missing. "
               "The public Solana RPC may be rate-limited; try another endpoint or retry later.")
        st = "partial"
    elif score >= 70:
        vzh, ven, st = f"综合风险 {score} 分：高危，快跑。", f"Risk {score}: DANGER.", "ok"
    elif score >= 40:
        vzh, ven, st = f"综合风险 {score} 分：中等风险，谨慎。", f"Risk {score}: medium risk.", "ok"
    else:
        vzh = (f"综合风险 {score} 分：可验证的指标相对健康，只用亏得起的钱。"
               + ("（部分数据缺失）" if blind else ""))
        ven = ("Risk score {score}: verifiable metrics relatively healthy — risk only what you "
               "can lose.".format(score=score) + (" (incomplete data)" if blind else ""))
        st = "ok"
    modules.append(mod("score", "综合风险评分", "Risk Score", st,
                       {"score": None if len(blind) >= 2 else score, "factors": factors}, vzh, ven))
    ai = ai_verdict(dex, modules, score)
    modules.append(mod("ai", "AI 综合研判", "AI Verdict",
                       ai["status"], ai["data"], ai["zh"], ai["en"]))
    return {"chain": "Solana", "contract": mint, "market": dex, "modules": modules}


# ---------------- formatting ----------------

def fmt_money(v):
    if v is None:
        return "未知"
    if v >= 1e9:
        return f"{v/1e9:.2f}B"
    if v >= 1e6:
        return f"{v/1e6:.2f}M"
    if v >= 1e3:
        return f"{v/1e3:.2f}K"
    return f"{v:.2f}"


def fmt_num(v):
    if v is None:
        return "未知"
    if v >= 1e9:
        return f"{v/1e9:.3f}B"
    if v >= 1e6:
        return f"{v/1e6:.3f}M"
    if v >= 1e3:
        return f"{v/1e3:.2f}K"
    return f"{v:,.0f}"
