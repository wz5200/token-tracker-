# 合约钱包源头追踪 · 自托管部署版

只读链上体检工具：输入 BSC / World Chain 合约地址或 Solana mint，输出大户源头追踪、
创建者追踪、创建者深度画像（批量发币检测/资金来源）、宣发线索一键搜、持仓集中度、
老鼠仓检测、LP 锁仓/销毁、开发者画像、大额异动、刷量检测、AI 综合研判和 0-100
综合风险评分（中英双语）。不交易、不连钱包。

## 快速启动（本机 / 服务器）

```bash
cd token-tracker
cp .env.example .env
# 把你的 BscScan 免费 Key 填进 .env（https://bscscan.com/register 免费注册）
# 没有 Key 也能跑：行情/供应量/LP/Solana 大户榜可用，大户榜与转账追踪会显示"缺数据"

docker compose up -d --build
# 打开 http://服务器IP:8080
```

无 Docker 时：

```bash
pip install -r requirements.txt
python app.py
```

## 数据来源

- 行情/交易对：DexScreener（免 Key，支持 BSC/World Chain/Solana）
- EVM 总供应量/decimals/余额/LP：各链公开 RPC（免 Key）
- EVM 大户榜/转账/创建者：Etherscan V2 统一接口（chainid 56=BSC / 480=World Chain，需一把免费 Etherscan 系 Key）
- Solana 大户榜/供应量：Solana 公开 RPC（免 Key）

## 对外发布

- 建议前面加反向代理（Nginx/Caddy）配 HTTPS，或用 Cloudflare Tunnel 暴露。
- 公开给别人用时，每位访客独立查询，无账号体系。
