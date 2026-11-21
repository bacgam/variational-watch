# variational-watch

Variational(Omni)의 $VAR TGE·포인트 관련 변화를 5분마다 확인해 텔레그램으로 알린다. GitHub Actions에서 돌아가므로 내 컴퓨터는 꺼져 있어도 된다.

## 무엇을 알리나

| 표시 | 내용 |
|---|---|
| 🚨 | TGE·포인트 신호: 바뀐 문서 줄·새 페이지·하위 주소·지갑 호출에 airdrop, claim, merkle, distribut, vesting, tge, points, snapshot, unlock, allocation, genesis, VAR. 팀 지갑의 새 컨트랙트 배포, 이름에 Variational이 들어간 컨트랙트와의 첫 거래도 🚨 |
| 📄 | 공식 문서(docs.variational.io) 변경(1시간마다). $VAR·리워드·포인트·로드맵·컨트랙트 주소 페이지는 바뀌면 항상, 나머지 페이지는 바뀐 줄에 위 단어가 있을 때만. 새 페이지·사라진 페이지(🗑️)도 알림 |
| 🌐 | variational.io 아래 새 하위 주소의 인증서 발급(Cert Spotter API, 1시간마다). 링크는 사이트가 아니라 인증서 기록 |
| 👛 | 팀 지갑이 처음 보는 종류의 컨트랙트를 호출함. 종류는 코드 지문(코드 길이+함수 목록)으로 가르므로, 정산 풀 생성·USDC 전송처럼 같은 종류를 반복하는 일상 거래는 뺌 |
| ⚠️ ✅ | 수집 3번 연속 실패 / 그 뒤 복구 |

팀 지갑은 공식 문서 [Mainnet Contracts](https://docs.variational.io/technical-documentation/mainnet-contracts)의 주소에서 정한다. 지갑인 주소(Protocol Treasury, Core OLP Vault)는 그대로 쓰고, 컨트랙트(Settlement Pool Factory, Oracle)는 배포한 지갑을 첫 실행 때 찾아 감시한다. 이 지갑들은 Arbitrum·Ethereum에서 본다.

테스트넷(Omni 테스트넷 = Arbitrum Sepolia)은 테스트넷 정산 풀을 만드는 운영 지갑(`TESTNET_WALLETS`)을 본다.

키워드는 `watch.py` 위쪽의 `AIRDROP`, 항상 알릴 문서는 `KEY_PAGES`, 공식 주소는 `OFFICIAL`, 테스트넷 지갑은 `TESTNET_WALLETS`, 도메인은 `DOMAINS`에서 고친다.

## 설정 (처음 한 번)

1. **텔레그램**: ink-watch와 같은 봇·채팅 ID를 써도 된다. 새로 만들려면 `@BotFather` → `/newbot`, 봇에게 메시지를 보낸 뒤 `https://api.telegram.org/bot<토큰>/getUpdates`에서 `"chat":{"id": 숫자`를 확인한다.
2. **Etherscan API 키(무료)**: etherscan.io 가입 → API Dashboard → Add. Arbitrum·Ethereum 모두 무료 등급에서 된다. 키가 없으면 팀 지갑 감시만 꺼지고 나머지는 돈다.
3. **Secrets 등록**: 저장소 → Settings → Secrets and variables → Actions → New repository secret
   - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `ETHERSCAN_API_KEY`
4. **첫 실행**: Actions 탭 → variational-watch → Run workflow. 첫 실행은 기준점만 잡고 알림을 보내지 않는다(설정 확인 메시지만 옴).
5. **외부 알람**: GitHub 자체 예약 실행은 잘 안 돌 때가 있어, ink-watch처럼 cron-job.org가 5분마다 아래를 호출하게 한다.
   - URL: `https://api.github.com/repos/bacgam/variational-watch/actions/workflows/watch.yml/dispatches` (POST)
   - 헤더: `Authorization: Bearer <이 저장소 Actions 읽기·쓰기 권한의 fine-grained 토큰>`, `Accept: application/vnd.github+json`
   - 본문: `{"ref":"main","inputs":{"quiet":"true"}}`

## 로컬 테스트

```
python watch.py
```

환경변수가 없으면 알림 내용을 화면에만 출력한다. 마지막으로 본 위치는 `state.json`에 저장된다.

## 알아 둘 점

- 포인트 잔고·리더보드는 공개 API가 없어 보지 않는다. 주간 포인트 배포·TGE 공지는 공식 텔레그램 공지 채널 [t.me/variational_io](https://t.me/variational_io)에 올라온다.
- `*.variational.io` 와일드카드 인증서가 있어서, 그 인증서로 덮인 하위 주소는 기록에 이름이 남지 않아 못 잡는다.
- 팀 지갑 감시는 공식 문서에 나온 주소와 그 배포자만 본다. 토큰을 다른 새 지갑으로 배포하면 놓친다(그 경우 문서·공지가 먼저 나올 가능성이 크다).
- 새 하위 주소·새 컨트랙트 알림은 "곧 뭔가 열린다"는 신호로만 쓴다. 접속·지갑 연결·서명은 공식 공지 뒤에.
