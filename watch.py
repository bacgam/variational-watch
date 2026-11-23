"""Variational($VAR) TGE·포인트 관련 변화를 감시해 텔레그램으로 알린다.

감시 대상: 공식 문서(docs.variational.io) 변경, variational.io 새 하위 주소 인증서, 팀 지갑(Arbitrum·Ethereum).
GitHub Actions에서 5분마다 실행된다. 텔레그램 설정(환경변수)이 없으면 알림을 화면에 출력만 한다.
마지막으로 본 위치는 state.json에 저장한다. 처음 실행할 때는 기준점만 잡고 알림을 보내지 않는다.
"""
import difflib
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
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
# 체인: (Etherscan chainid, 탐색기)
CHAINS = {
    "Arbitrum": (42161, "https://arbiscan.io"),
    "Ethereum": (1, "https://etherscan.io"),
    "Arbitrum Sepolia": (421614, "https://sepolia.arbiscan.io"),
}
MAINNET_CHAINS = ["Arbitrum", "Ethereum"]  # 공식 주소에서 찾은 팀 지갑을 볼 체인. 토큰은 Ethereum에 나올 수도 있어 둘 다 본다
# Omni 테스트넷(Arbitrum Sepolia) 지갑. 출처: 테스트넷 입금 대상 정산 풀 0x3e96…21E3의 생성자(거래 100만 건 넘는 운영 지갑)
TESTNET_WALLETS = {"0xc27e00de4eafffa60b6b008c4520517ed00294cb": "테스트넷 운영 지갑"}

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
    if "jsonrpc" in j:  # module=proxy 응답은 RPC 형식
        if "error" in j:
            raise RuntimeError(f"Etherscan: {j['error']}")
        return j["result"]
    if j.get("status") == "1":
        return j["result"]
    if str(j.get("message", "")).startswith("No ") or str(j.get("result", "")).startswith("No "):
        return []  # No transactions found / No data found
    if isinstance(j.get("result"), str):  # 키 오류·한도 초과 등은 result에 문장으로 온다
        raise RuntimeError(f"Etherscan: {j['result']}")
    return []


def doc_lines(text):
    """비교할 줄. GitBook이 모든 페이지 맨 위에 자동으로 붙이는 안내문 줄은 뺀다(플랫폼이 바꾸면 전 페이지가 오탐)."""
    return [ln for ln in text.splitlines() if not ln.startswith("> For the complete documentation index")]


def clip(ln, others):
    """긴 줄은 처음 200자 대신, 반대편에서 가장 비슷한 줄과 달라지는 곳 근처 200자를 보여 준다."""
    i = max((len(os.path.commonprefix([ln, o])) for o in others), default=0)
    s = max(0, i - 60)
    return ("…" if s else "") + ln[s:s + 200]


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
            changed = [ln for ln in difflib.unified_diff(doc_lines(old[u]), doc_lines(text), lineterm="", n=0)
                       if ln[:1] in "+-" and not ln.startswith(("+++", "---")) and ln[1:].strip()]
            if not changed:
                continue
            hot = any(AIRDROP.search(ln) for ln in changed)
            if u not in KEY_PAGES and not hot:
                continue
            body = "\n".join(("지금: " if ln[0] == "+" else "이전: ")
                             + clip(ln[1:], [o[1:] for o in changed if o[0] != ln[0]]) for ln in changed[:8])
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


def fingerprint(chainid, addr, cache):
    """컨트랙트 종류를 가르는 코드 지문: 코드 길이 + 함수 선택자 목록. 프록시면 실제 구현 코드로 본다.
    같은 틀로 찍어낸 컨트랙트(정산 풀 등)는 내부 값(immutable)만 달라서 지문이 같다."""
    key = f"{chainid}:{addr}"
    if key not in cache:
        impl = etherscan(chainid, module="proxy", action="eth_getStorageAt", address=addr, tag="latest",
                         position="0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc")  # EIP-1967 구현 주소 칸
        if int(impl or "0x0", 16):
            addr = "0x" + impl[-40:]
        b = bytes.fromhex(etherscan(chainid, module="proxy", action="eth_getCode", address=addr, tag="latest")[2:])
        sels, i = set(), 0
        while i < len(b):
            if b[i] == 0x63:  # PUSH4: 함수 선택자
                sels.add(b[i + 1:i + 5].hex())
            i += 1 + (b[i] - 0x5f if 0x60 <= b[i] <= 0x7f else 0)
        cache[key] = hashlib.sha1(f"{len(b)}:{','.join(sorted(sels))}".encode()).hexdigest()[:16]
        while len(cache) > 5000:  # 오래된 것부터 지운다
            cache.pop(next(iter(cache)))
    return cache[key]


def check_wallet(chain, addr, label, st, cache, out):
    """팀 지갑이 직접 보낸 거래 중 처음 보는 종류의 컨트랙트를 배포·호출한 것과 AIRDROP 단어가 걸린 호출을 out에 추가한다.
    종류는 코드 지문으로 가른다. 정산 풀 생성, USDC 전송처럼 같은 종류를 반복하는 일상 거래는 알리지 않는다."""
    chainid, scan = CHAINS[chain]
    last = st.get("block")
    txs = etherscan(chainid, module="account", action="txlist", address=addr,
                    startblock=0 if last is None else last + 1, sort="desc", page=1, offset=1000)
    mine = [t for t in txs if t["from"].lower() == addr and t.get("isError") != "1"]
    learning = last is None or "kinds" not in st  # 첫 실행(또는 지문 도입 전 상태): 알리지 않고 종류만 익힌다
    if last is None:
        mine = mine[:30]  # 조회 횟수를 아끼려고 최근 30건으로만 익힌다
    kinds = set(st.get("kinds", []))

    found = []
    for t in reversed(mine):  # 오래된 것부터
        made = (t.get("contractAddress") or "").lower()
        if not made and t.get("input", "0x") == "0x":  # ETH 단순 송금
            continue
        target = made or t["to"].lower()
        new = fingerprint(chainid, target, cache["codes"]) not in kinds
        kinds.add(cache["codes"][f"{chainid}:{target}"])
        if learning:
            continue
        method = t.get("functionName", "").split("(")[0] or t.get("methodId", "")
        if not (new or AIRDROP.search(method)):
            continue
        name = contract_name(chainid, target, cache["names"])  # 알릴 것만 이름을 조회한다
        if made:
            found.append(f"🚨 [{chain}] {label}: 새 컨트랙트 배포 {name or '이름 미확인(검증 전)'}\n{scan}/address/{made}")
            continue
        hot = AIRDROP.search(f"{method} {name}") or "variational" in name.lower()
        mark = "🚨" if hot else "👛"
        note = " (처음 보는 종류의 컨트랙트)" if new else ""
        found.append(f"{mark} [{chain}] {label}: {method} → {name or target[:10]}{note}\n{scan}/tx/{t['hash']}")

    st["block"] = max([int(t["blockNumber"]) for t in txs], default=last or 0)
    st["kinds"] = sorted(kinds)
    st.pop("targets", None)  # 예전 방식(주소별 기억)의 흔적
    out.extend(found)


def check_wallets(st, out):
    if not os.environ.get("ETHERSCAN_API_KEY"):
        print("ETHERSCAN_API_KEY 없음: 팀 지갑 감시 건너뜀", file=sys.stderr)
        return
    cache = {"names": st.setdefault("names", {}), "codes": st.setdefault("codes", {})}
    wallets = [(a, l, MAINNET_CHAINS) for a, l in team_wallets(st).items()]
    wallets += [(a, l, ["Arbitrum Sepolia"]) for a, l in TESTNET_WALLETS.items()]
    for addr, label, chains in wallets:
        for chain in chains:
            check_wallet(chain, addr, label, st.setdefault(f"{chain} {addr}", {}), cache, out)


def heartbeat(st, n_alerts, out):
    """주가 바뀐 뒤 첫 실행 때 지난주 실행·알림 횟수를 out에 추가한다. 이 메시지가 안 오면 봇이 멈춘 것.
    주 경계는 월요일 0시 UTC(= 한국 시간 오전 9시)."""
    y, w, _ = datetime.now(timezone.utc).isocalendar()
    week = f"{y}-W{w:02d}"
    if st.get("week") and st["week"] != week:
        out.append(f"💓 variational-watch 주간 점검: 정상 작동 중\n"
                   f"지난주 실행 {st['runs']:,}회(5분마다면 약 2,000회) · 알림 {st['alerts']}건")
        st.update(runs=0, alerts=0)
    st["week"] = week
    st["runs"] = st.get("runs", 0) + 1
    st["alerts"] = st.get("alerts", 0) + n_alerts


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

    heartbeat(state.setdefault("주간 점검", {}), len(alerts), alerts)

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
