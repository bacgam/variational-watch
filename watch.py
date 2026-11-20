"""Variational($VAR) TGE·포인트 관련 변화를 감시해 텔레그램으로 알린다.

감시 대상: 공식 문서(docs.variational.io) 변경, variational.io 새 하위 주소 인증서, 팀 지갑(Arbitrum·Ethereum).
GitHub Actions에서 5분마다 실행된다. 텔레그램 설정(환경변수)이 없으면 알림을 화면에 출력만 한다.
마지막으로 본 위치는 state.json에 저장한다. 처음 실행할 때는 기준점만 잡고 알림을 보내지 않는다.
"""
import difflib
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

STATE_FILE = "state.json"

DOCS = "https://docs.variational.io"
# 내용이 바뀌면 무조건 알릴 문서. 나머지 문서는 바뀐 줄에 AIRDROP 단어가 있을 때만 알린다
KEY_PAGES = {
    f"{DOCS}/token/usdvar.md",                              # $VAR 토큰
    f"{DOCS}/omni/rewards.md",                              # 리워드 티어
    f"{DOCS}/omni/rewards/points.md",                       # 포인트
    f"{DOCS}/getting-started/roadmap.md",                   # 로드맵 ('Launch of the $VAR token' 항목)
    f"{DOCS}/technical-documentation/mainnet-contracts.md",  # 공식 컨트랙트 주소
}
DOMAINS = ["variational.io"]  # 새 하위 주소(인증서)를 감시할 공식 도메인

# 공식 문서 Mainnet Contracts 페이지에 나온 Arbitrum 주소
OFFICIAL = {
    "0x5e91b40467fb8902c46a7b6cb90482363188d645": "Protocol Treasury",
    "0x74bbbb0e7f0bad6938509dd4b556a39a4db1f2cd": "Core OLP Vault",
    "0x0f820b9afc270d658a9fd7d16b1bdc45b70f074c": "Settlement Pool Factory",
    "0x84be56470d45b7f6629a66a219a38681f6ba6172": "Oracle",
}
# 팀 지갑을 볼 체인: (Etherscan chainid, 탐색기). 토큰은 Ethereum에 나올 수도 있어 같은 지갑을 둘 다 본다
CHAINS = {"Arbitrum": (42161, "https://arbiscan.io"), "Ethereum": (1, "https://etherscan.io")}
SKIP_METHODS = {"0xa9059cbb", "0x095ea7b3"}  # transfer, approve: 이미 상대한 컨트랙트면 알리지 않는다

# TGE·포인트 신호. 대문자 VAR만 토큰 기호로 본다(Variational·variable과 섞이지 않게)
AIRDROP = re.compile(r"(?i:airdrop|claim|merkle|distribut|vesting|\btge\b|\bpoints?\b|snapshot|unlock|allocation|genesis)|\bVAR\b")


def get(url, headers=None, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "variational-watch", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8")


def get_json(url, headers=None, timeout=30):
    return json.loads(get(url, headers, timeout))


def etherscan(chainid, **params):
    time.sleep(0.25)  # 무료 키 한도: 초당 5회
    q = urllib.parse.urlencode({"chainid": chainid, **params, "apikey": os.environ["ETHERSCAN_API_KEY"]})
    j = get_json(f"https://api.etherscan.io/v2/api?{q}")
    if j.get("status") == "1":
        return j["result"]
    if str(j.get("message", "")).startswith("No ") or str(j.get("result", "")).startswith("No "):
        return []  # No transactions found / No data found
    if isinstance(j.get("result"), str):  # 키 오류·한도 초과 등은 result에 문장으로 온다
        raise RuntimeError(f"Etherscan: {j['result']}")
    return []


def check_docs(st, out):
    """공식 문서에 새 페이지가 생기거나, 페이지 내용이 바뀌면 바뀐 줄을 out에 추가한다(1시간마다)."""
    now = time.time()
    if now - st.get("checked_at", 0) < 3600:
        return
    index = get(f"{DOCS}/llms.txt")  # 문서 전체 목록
    titles = {u: t for t, u in re.findall(r"\[([^\]]+)\]\((https://docs\.variational\.io/[^)]+\.md)\)", index)}
    old = st.get("pages")
    pages = {u: get(u) for u in titles}

    if old is not None:
        for u, text in pages.items():
            link = u[:-3]  # .md를 떼면 사람이 보는 페이지
            if u not in old:
                mark = "🚨" if AIRDROP.search(titles[u] + text) else "📄"
                out.append(f"{mark} [문서] 새 페이지: {titles[u]}\n{link}")
                continue
            if text == old[u]:
                continue
            changed = [ln for ln in difflib.unified_diff(old[u].splitlines(), text.splitlines(), lineterm="", n=0)
                       if ln[:1] in "+-" and not ln.startswith(("+++", "---")) and ln[1:].strip()]
            hot = any(AIRDROP.search(ln) for ln in changed)
            if u not in KEY_PAGES and not hot:
                continue
            body = "\n".join(("지금: " if ln[0] == "+" else "이전: ") + ln[1:200] for ln in changed[:8])
            if len(changed) > 8:
                body += f"\n… 외 {len(changed) - 8}줄"
            out.append(f"{'🚨' if hot else '📄'} [문서] 바뀜: {titles[u]}\n{body}\n{link}")
        for u in old:
            if u not in pages:
                out.append(f"🗑️ [문서] 페이지 사라짐: {u[:-3]}")
    st["pages"] = pages
    st["checked_at"] = now


def check_subdomains(domain, st, out):
    """공식 도메인 아래 새 하위 주소의 인증서가 발급되면 out에 추가한다(인증서 공개 기록, Cert Spotter API, 1시간마다).
    도메인 관리자만 인증서를 받을 수 있으므로 사칭 도메인은 섞이지 않는다.
    마지막으로 본 발급 번호(after) 이후 것만 받는다. 과거 기록을 다 따라잡기 전(synced 전)에는 알리지 않는다."""
    now = time.time()
    if now - st.get("checked_at", 0) < 3600:
        return
    synced = st.get("synced", False)
    known = set(st.get("names", []))
    names, after = set(), st.get("after")
    for _ in range(8):  # 한 번에 100건씩. 무료 조회 한도가 작아 한 시간에 8번까지만
        url = f"https://api.certspotter.com/v1/issuances?domain={domain}&include_subdomains=true&expand=dns_names"
        if after:
            url += f"&after={after}"
        page = get_json(url)
        for c in page:
            names.update(n.lower() for n in c["dns_names"] if n.lower() == domain or n.lower().endswith("." + domain))
        if page:
            after = page[-1]["id"]
        if len(page) < 100:
            st["synced"] = True
            break
    if synced:
        for n in sorted(names - known):
            mark = "🚨" if AIRDROP.search(n) else "🌐"
            # 링크는 사이트가 아니라 인증서 기록. 확인 안 된 사이트에 바로 들어가지 않게
            out.append(f"{mark} [서브도메인] 새 인증서: {n}\nhttps://crt.sh/?q={n}")
    st["names"] = sorted(known | names)
    st["after"] = after
    st["checked_at"] = now


def contract_name(chainid, addr, cache):
    """검증된 컨트랙트 이름. 프록시면 실제 코드 이름을 붙인다. 검증 전이면 빈 문자열."""
    key = f"{chainid}:{addr}"
    if key not in cache:
        r = (etherscan(chainid, module="contract", action="getsourcecode", address=addr) or [{}])[0]
        name = r.get("ContractName") or ""
        if r.get("Proxy") == "1" and r.get("Implementation"):
            impl = (etherscan(chainid, module="contract", action="getsourcecode", address=r["Implementation"]) or [{}])[0]
            name = f"{name}→{impl.get('ContractName') or '?'}"
        cache[key] = name
    return cache[key]


def team_wallets(st):
    """감시할 지갑: 공식 주소 중 지갑(EOA)인 것 + 공식 컨트랙트를 배포한 지갑. 처음 한 번 찾아 저장한다."""
    if "wallets" in st:
        return st["wallets"]
    wallets, creators = {}, {}
    for addr, label in OFFICIAL.items():
        r = etherscan(42161, module="contract", action="getcontractcreation", contractaddresses=addr)
        if not r:  # 배포 기록이 없으면 컨트랙트가 아니라 지갑
            wallets[addr] = label
        else:
            creators.setdefault(r[0]["contractCreator"].lower(), []).append(label)
    for c, labels in creators.items():
        if etherscan(42161, module="contract", action="getcontractcreation", contractaddresses=c):
            print(f"{'·'.join(labels)} 배포자 {c}는 컨트랙트(팩토리)라 감시하지 않음", file=sys.stderr)
            continue
        wallets[c] = "·".join(labels) + " 배포자"
    st["wallets"] = wallets
    return wallets


def check_wallet(chain, addr, label, st, cache, out):
    """팀 지갑이 직접 보낸 거래 중 컨트랙트 배포, 처음 상대하는 컨트랙트 호출, AIRDROP 단어가 걸린 호출을 out에 추가한다.
    같은 컨트랙트를 반복해서 부르는 일상 거래(정산·USDC 전송 등)는 알리지 않는다."""
    chainid, scan = CHAINS[chain]
    last = st.get("block")
    txs = etherscan(chainid, module="account", action="txlist", address=addr,
                    startblock=0 if last is None else last + 1, sort="desc", page=1, offset=100)
    mine = [t for t in txs if t["from"].lower() == addr and t.get("isError") != "1"]
    targets = set(st.get("targets", []))
    if last is None:  # 첫 실행: 기준점만 잡는다
        st["block"] = max([int(t["blockNumber"]) for t in txs], default=0)
        st["targets"] = sorted({t["to"].lower() for t in mine if t["to"]})
        return

    found = []
    for t in reversed(mine):  # 오래된 것부터
        if t.get("contractAddress"):  # 컨트랙트 배포
            made = t["contractAddress"].lower()
            name = contract_name(chainid, made, cache) or "이름 미확인(검증 전)"
            found.append(f"🚨 [{chain}] {label}: 새 컨트랙트 배포 {name}\n{scan}/address/{made}")
            continue
        to = t["to"].lower()
        method = t.get("functionName", "").split("(")[0] or t.get("methodId", "")
        if t.get("input", "0x") == "0x":  # ETH 단순 송금
            continue
        first = to not in targets
        targets.add(to)
        if not first and t.get("methodId") in SKIP_METHODS:
            continue
        name = contract_name(chainid, to, cache) or to[:10]
        hot = AIRDROP.search(f"{method} {name}") or "variational" in name.lower()
        if first or hot:
            mark = "🚨" if hot else "👛"
            note = " (처음 상대하는 컨트랙트)" if first else ""
            found.append(f"{mark} [{chain}] {label}: {method} → {name}{note}\n{scan}/tx/{t['hash']}")

    st["block"] = max([int(t["blockNumber"]) for t in txs], default=last)
    st["targets"] = sorted(targets)
    out.extend(found)


def check_wallets(st, out):
    if not os.environ.get("ETHERSCAN_API_KEY"):
        print("ETHERSCAN_API_KEY 없음: 팀 지갑 감시 건너뜀", file=sys.stderr)
        return
    cache = st.setdefault("names", {})
    for addr, label in team_wallets(st).items():
        for chain in CHAINS:
            check_wallet(chain, addr, label, st.setdefault(f"{chain} {addr}", {}), cache, out)


def send(text):
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        print("--- 텔레그램 설정 없음, 출력만 함 ---\n" + text + "\n")
        return
    data = urllib.parse.urlencode({"chat_id": chat, "text": text, "disable_web_page_preview": "true"}).encode()
    urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data, timeout=30)


def main():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            state = json.load(f)
    except FileNotFoundError:
        state = {}

    sources = [("공식 문서", check_docs), ("팀 지갑", check_wallets)]
    for d in DOMAINS:
        sources.append((f"서브도메인 {d}", lambda st, out, d=d: check_subdomains(d, st, out)))

    alerts = []
    for name, fn in sources:
        st = state.setdefault(name, {})
        try:
            fn(st, alerts)
            if st.pop("fails", 0) >= 3:
                alerts.append(f"✅ {name} 수집 복구")
        except Exception as e:  # 한 곳이 실패해도 나머지는 계속. 일시 장애는 넘기고 3번 연속 실패 때만 알린다
            print(f"{name} 실패: {e!r}", file=sys.stderr)
            st["fails"] = st.get("fails", 0) + 1
            if st["fails"] == 3:
                alerts.append(f"⚠️ {name} 수집 3번 연속 실패: {e!r}")

    # Actions 탭에서 수동 실행하면 새 소식이 없어도 확인 메시지를 보낸다(설정 점검용). 외부 알람(QUIET)은 제외
    if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch" and os.environ.get("QUIET") != "true":
        if not os.environ.get("ETHERSCAN_API_KEY"):
            alerts.append("⚠️ ETHERSCAN_API_KEY 없음: 팀 지갑 감시가 꺼져 있음")
        if not alerts:
            alerts.append("✅ variational-watch 수동 실행 완료: 새 소식 없음")

    # 텔레그램 메시지 한도(4096자) 안에서 묶어 보낸다
    chunk = ""
    for a in alerts:
        if len(chunk) + len(a) > 3800:
            send(chunk)
            chunk = ""
        chunk += a[:3800] + "\n\n"
    if chunk:
        send(chunk)

    # 전송이 끝난 뒤에만 저장한다(전송 실패 시 다음 실행에서 다시 보냄)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    print(f"완료: 알림 {len(alerts)}건")


if __name__ == "__main__":
    main()
