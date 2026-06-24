"""M5 真实 HTTP 全链路验收（M4 鉴权版 · 本机 curl 被治理 deny，httpx 等价）。

走：种邀请码 → redeem 换 token → health → precheck(事实题拦/决策题放行)
   → start/stream(SSE) → respond×N → stance(412 硬门) → reveal → memo
   → 越权 401 → 断点恢复 → 画像观察 → 配额剩余。全部真实模型调用。
"""
from __future__ import annotations

import json
import sys

import httpx

sys.path.insert(0, __file__.rsplit("/tools/", 1)[0] + "/src")
from sparring.auth import AuthStore  # noqa: E402
from sparring.config import load_config  # noqa: E402

BASE = "http://127.0.0.1:8788"

QUESTION = (
    "我是非技术背景的独立开发者，主线是做'判断力'主题的内容变现。"
    "手里有个停滞两个月、0用户的旧产品，其中的苏格拉底对练功能比较独特。"
    "我该把它抽成独立小产品并行推进，还是封存它专注内容主线？"
)
REPLIES = [
    "我担心两边一起做最后都做不好，但又舍不得这个功能的独特性，它和我的判断力主题其实是一体的。",
    "想清楚了：先专注内容主线三个月，把对练功能做成内容里的互动环节试水，有真实拉力再单独产品化。因为我的获客和变现都在内容侧。",
]


def main() -> int:
    cfg = load_config()
    code = AuthStore(cfg.db_path).create_invite("e2e-live")
    c = httpx.Client(timeout=200)

    r = c.get(f"{BASE}/api/health")
    print(f"[health] {r.status_code} {r.text}")

    # 无 token 越权：业务端点应 401
    r = c.get(f"{BASE}/api/history")
    print(f"[无token查历史] {r.status_code}（预期 401）")
    assert r.status_code == 401

    r = c.post(f"{BASE}/api/auth/redeem", json={"code": code})
    token = r.json()["token"]
    print(f"[redeem] {r.status_code} · token 取得 · user={r.json()['user_id']}")
    H = {"Authorization": f"Bearer {token}"}
    # 画像默认关（审查），显式开启才观察
    c.post(f"{BASE}/api/profile/consent", json={"consent": True}, headers=H)
    print("[consent] 已显式开启跨会话观察（默认关）")

    # 邀请码一码一人：第二次 redeem 同码必 400
    r2 = c.post(f"{BASE}/api/auth/redeem", json={"code": code})
    print(f"[同码二次 redeem] {r2.status_code}（预期 400）")
    assert r2.status_code == 400

    r = c.post(f"{BASE}/api/sparring/precheck", json={"question": "RTX 5090 的显存带宽是多少？"}, headers=H)
    g = r.json()
    print(f"[precheck·事实题] passed={g['passed']} conf={g['confidence']} | 改写: {g['rewrite_suggestion'][:50]}")
    assert g["passed"] is False

    r = c.post(f"{BASE}/api/sparring/precheck", json={"question": QUESTION}, headers=H)
    print(f"[precheck·决策题] passed={r.json()['passed']}")
    assert r.json()["passed"] is True

    print("[start/stream] SSE：")
    session_id, first_guide = "", ""
    with c.stream("POST", f"{BASE}/api/sparring/start/stream", json={"question": QUESTION}, headers=H) as resp:
        event = ""
        for line in resp.iter_lines():
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:") and event:
                data = json.loads(line.split(":", 1)[1].strip())
                brief = {k: v for k, v in data.items() if k in ("arrived", "total", "contributors", "divergence_topics")}
                print(f"  ← {event}: {json.dumps(brief, ensure_ascii=False)[:150]}")
                if event == "coach_ready":
                    session_id, first_guide = data["session_id"], data["guide_message"]
                if event == "error":
                    print(f"  !! {data}")
                    return 1
    assert session_id
    print(f"[教练第一问] {first_guide[:110]}")

    for i, text in enumerate(REPLIES, 1):
        d = c.post(f"{BASE}/api/sparring/{session_id}/respond", json={"text": text}, headers=H).json()
        print(f"[respond {i}] type={d['type']} closure={d.get('closure', '-')} | {d['message'][:90]}")
        if d["type"] == "suggest_stance":
            break

    r = c.post(f"{BASE}/api/sparring/{session_id}/reveal", headers=H)
    print(f"[reveal·未写立场] {r.status_code}（预期 412）")
    assert r.status_code == 412

    r = c.post(
        f"{BASE}/api/sparring/{session_id}/stance",
        json={"stance": "三个月专注内容主线，对练功能以内容互动试水", "reason": "获客变现都在内容侧；0用户说明独立入口未验证"},
        headers=H,
    )
    print(f"[stance] {r.status_code}")

    out = c.post(f"{BASE}/api/sparring/{session_id}/reveal", headers=H).json()
    print(f"[reveal] 综合头120字: {out['synthesis'][:120]}")
    print(f"  对照·你漏了: {out['comparison']['you_missed']}")

    r = c.get(f"{BASE}/api/sparring/{session_id}/memo", headers=H)
    print(f"[memo] {r.status_code} · {len(r.text)} 字")

    # 越权：换个 token 读这个 session 应 404
    other = AuthStore(cfg.db_path).create_invite("e2e-intruder")
    ot = c.post(f"{BASE}/api/auth/redeem", json={"code": other}).json()["token"]
    r = c.get(f"{BASE}/api/sparring/{session_id}", headers={"Authorization": f"Bearer {ot}"})
    print(f"[他人 token 越权读] {r.status_code}（预期 404）")
    assert r.status_code == 404

    # 画像观察（consent 默认开，reveal 已触发）
    p = c.get(f"{BASE}/api/profile", headers=H).json()
    print(f"[画像] consent={p['consent']} 观察数={len(p['observations'])} 配额剩余={p['quota_remaining']}")
    if p["observations"]:
        o = p["observations"][0]
        print(f"  观察样例: 「{o['behavior_tag']}」({o['dimension']}) ×{o['repeat_count']}")

    print("\nM5 全链路（M4 鉴权版）通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
