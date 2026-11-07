# 주도주 스크리너

코스피 200 · 코스닥 150 · 나스닥 100 · S&P 500 구성종목을 매일 장 마감 후 자동으로 점수화하고,
GitHub Pages 웹 대시보드에 후보군 · 회사 소개 · 5개년 재무를 보여준다.

판정 기준: `reference/leader_rules.md`
(DB금융투자 「주도주의 생로병사」, 하나증권 「주도주의 생애주기」, 신영증권 「글로벌 전략」 + CAN SLIM)

## 실행 시각
| 실행 | 시각 (KST) | 대상 |
|---|---|---|
| 한국 | 월~금 16:40 | 코스피 200, 코스닥 150 |
| 미국 | 화~토 06:40 | 나스닥 100, S&P 500 |

GitHub 예약 실행은 수십 분 늦어질 수 있다.

## 설치 (처음 한 번)

1. **저장소 만들기** — GitHub에서 새 저장소(예: `leader-screener`)를 만들고 이 폴더의 파일을 전부 올린다.
   - 웹에서 올릴 때: 저장소 첫 화면 → `uploading an existing file` → 압축을 푼 폴더 안의 내용을 통째로 끌어다 놓기 (`.github` 폴더 포함 확인)
   - GitHub Pages 무료 사용은 **Public 저장소**만 가능 (Private는 유료 플랜 필요)
2. **쓰기 권한** — Settings → Actions → General → Workflow permissions → **Read and write permissions** 선택 → Save
3. **웹 대시보드 켜기** — Settings → Pages → Source: **Deploy from a branch**, Branch: **main**, 폴더: **/docs** → Save
   - 주소: `https://<아이디>.github.io/leader-screener/`
4. **비밀값 (선택, 권장)** — Settings → Secrets and variables → Actions → New repository secret

   | 이름 | 용도 | 없으면 |
   |---|---|---|
   | `DART_API_KEY` | 한국 종목 5~6개년 매출·영업이익·순이익 (opendart.fss.or.kr 무료 발급) | yfinance 4개년 |
   | `SEC_USER_AGENT` | 미국 종목 5~6개년 10-K 재무. 값 예: `Hong Gildong hong@example.com` (SEC가 연락처 기재를 요구) | yfinance 4개년 |
   | `ANTHROPIC_API_KEY` | 지수별 상위 10종목 한국어 회사 소개 + 규칙 기반 판정 코멘트 (같은 종목은 7일간 재사용) | 영문 회사 소개만 표시 |

5. **첫 실행** — Actions 탭 → `주도주 스크리너` → Run workflow → markets `KR,US`, full **체크** → Run
   - 첫 실행은 재무를 전부 받느라 1~2시간 걸릴 수 있다. 이후 매일 실행은 재무를 7일 주기로 나눠 갱신한다.

## 장세 시작일 바꾸기
DB 규칙(장세 시작 후 모멘텀 상위 5%, 장세 고점 대비 -30%)은 기준일이 필요하다.
현재 `config.json` 은 2025년 4월 관세 쇼크 바닥(한국 2025-04-09, 미국 2025-04-08)으로 설정돼 있다.
이번 하락의 바닥을 확인하면 `config.json` 의 `base_date` 를 그 날짜로 바꾸면 새 장세 기준으로 다시 계산된다.
`"auto"` 로 두면 최근 1년 지수 최저 종가일을 쓴다.

## 점수 (100점 환산)
| 항목 | 배점 |
|---|---|
| C 분기 EPS YoY ≥25% (흑자전환 8점) | 15 |
| A 3년 EPS 연평균 ≥25% + ROE ≥17% | 15 |
| N 52주 고점 -15% 이내 | 10 |
| S 50일 상승/하락 거래량비 ≥1 | 10 |
| L RS(3·6·9·12개월 가중 수익률) 지수 내 상위 20% | 15 |
| I 기관 보유 ≥30% (미국만, 한국은 제외 후 환산) | 5 |
| DB 장세 시작 후 수익률 상위 5% | 7 |
| DB 장세 고점 대비 -30% 이내 | 5 |
| DB 10주선 > 40주선 | 3 |
| 하나 매출 성장 ≥20% | 5 |
| 하나 영업이익률 상승 | 5 |
| 신영 범용재·자본집약 업종 | -5 |

판정: 주도주 후보(70점↑ + CAN SLIM 4개↑ + RS 70↑) / 관찰(50점↑) / 약함 / 탈락(장세 고점 대비 -30% 초과).
M(시장 방향)은 점수에 넣지 않고 상단 배너로 표시한다. M이 하락 추세면 전부 관찰용이다.

## 구성종목 수집이 실패할 때
- 코스피 200: pykrx → 네이버 금융 순서로 시도, 둘 다 실패하면 `data/constituents/KOSPI200.csv` 캐시 사용
- 코스닥 150: pykrx, 실패하면 캐시. 캐시가 없으면 HTS에서 구성종목을 내려받아
  `data/constituents/KOSDAQ150.csv` 로 올린다 (컬럼: `code,symbol,name,sector`, symbol 예: `086450.KQ`)
- 나스닥 100 / S&P 500: 위키백과 표

## 로컬 테스트
```
pip install -r requirements.txt
python -m screener.main --markets KR        # 실제 데이터
python -m tests.mock_run                     # 네트워크 없이 합성 데이터 (docs/data 덮어씀, 확인 후 삭제)
cd docs && python -m http.server 8000        # http://localhost:8000
```

투자 판단은 본인 책임이며, 점수는 규칙 대비 위치를 보여줄 뿐 매수 권유가 아니다.
