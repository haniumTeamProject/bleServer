> **단계별로 따라하려면 `docs/로컬AI_따라하기.md` 를 보세요.**
> 이 문서는 설치·설정 요약입니다.

# Ollama 설치 — 음성 목적지 해석용

목적지를 말로 알아듣는 부분(`app/ws/llm_matcher.py`)이 쓰는 로컬 LLM.

**안 깔아도 서버는 돌아간다.** 규칙 엔진으로 자동으로 넘어가서, 방 번호("407호로 가줘")와
종류+순번("두 번째 화장실")은 그대로 동작한다. 다만 **동의어("변소", "승강기")와
문장형 발화**는 못 알아듣는다.

---

## 0. 어디에 깔아야 하나 ← 가장 자주 틀리는 부분

**FastAPI 서버가 도는 그 컴퓨터에 깐다.** 폰이나 개발용 노트북이 아니다.

```
[안드로이드 폰]  ──웹소켓──▶  [FastAPI 서버]  ──HTTP──▶  [Ollama]
                              여기와 같은 기계 ────────────┘
```

`llm_matcher.py` 가 `http://localhost:11434` 로 부르기 때문이다.
서버가 `hanium.mcsmtp.org` 에 있으면 **거기에** 깔아야 한다.

굳이 다른 기계에 두려면 6장을 참고. 다만 실내 안내는 응답이 빨라야 해서
같은 기계에 두는 쪽이 낫다.

---

## 1. 설치

### Windows

[ollama.com/download](https://ollama.com/download) 에서 설치 파일을 받아 실행한다.
설치하면 백그라운드에서 자동으로 뜬다.

### Linux (서버가 리눅스면 이쪽)

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

서비스로 등록되므로 재부팅해도 자동으로 뜬다.

```bash
sudo systemctl status ollama      # 떠 있는지 확인
sudo systemctl enable --now ollama
```

### macOS

```bash
brew install ollama
brew services start ollama
```

---

## 2. 모델 받기

```bash
ollama pull exaone3.5:7.8b
```

LG AI연구원의 **EXAONE 3.5** — 한국어·영어 이중 언어 모델이라 한국어 표현을 잘 잡는다.
(EXAONE 4.0은 아직 Ollama 공식 지원이 없어서 3.5를 쓴다.)

### 서버 사양에 맞게 고르기

| 모델 | 내려받기 | 필요 메모리(대략) | 어떤 경우에 |
|---|---|---|---|
| `exaone3.5:2.4b` | 1.6GB | 4GB 이상 | **작은 VPS·라즈베리파이.** 빠르지만 표현 이해가 얕다 |
| `exaone3.5:7.8b` | 4.8GB | 8GB 이상 | **기본값. 웬만하면 이것** |
| `exaone3.5:32b` | 19GB | 32GB 이상 | GPU가 있을 때 |

> 메모리가 모자라면 Ollama가 모델을 못 올리고 요청이 실패한다. 그러면 서버는
> 규칙 엔진으로 조용히 넘어가므로 **증상이 "동의어만 안 됨"으로 보인다.**
> 로그에 `[목적지] LLM 예열 실패` 가 찍혔는지 꼭 확인할 것.

작은 모델로 바꾸려면 환경변수만 고치면 된다.

```bash
export LLM_MODEL=exaone3.5:2.4b
```

한국어가 되는 다른 모델도 쓸 수 있다. `qwen3:8b`, `gemma3` 등.

---

## 3. 잘 깔렸는지 확인

```bash
ollama list                    # 받은 모델 목록
curl http://localhost:11434/api/tags     # 서버가 응답하는지
ollama run exaone3.5:7.8b "안녕하세요"    # 직접 대화해보기
```

---

## 4. 서버와 연결됐는지 확인

FastAPI를 띄우면 **뜰 때 모델을 미리 깨운다.** 로그를 보면 된다.

```
[목적지] LLM 준비 완료: exaone3.5:7.8b (23.4초)      ← 정상
[목적지] LLM 예열 실패(ollama/exaone3.5:7.8b): ...   ← Ollama가 안 떠 있음
```

매칭까지 직접 보려면:

```bash
python tests/test_llm_matcher.py --live
```

```
'407호로 가줘'    → resolved   407                (0.42초)
'변소 급해요'      → ambiguous  ['화장실 1', '화장실 2']  (0.68초)
'승강기 어딨어요'   → ambiguous  ['엘리베이터 1', '엘리베이터 2'] (0.55초)
```

---

## 5. 왜 예열을 하나 ← 데모에서 중요

Ollama는 **쓰지 않으면 5분 뒤 모델을 메모리에서 내린다.** 그러면 다음 요청에서
다시 올리느라 수십 초가 걸리는데, 평소 타임아웃은 6초라 **그 요청은 무조건 실패**하고
규칙 엔진으로 떨어진다.

하필 그게 **데모의 첫 시연**이 되기 쉽다. 그래서 두 가지를 해뒀다.

```python
# 서버 기동 시 백그라운드로 한 번 깨운다 (llm_matcher.warmup)
WARMUP_TIMEOUT_S = 120     # 예열은 넉넉하게
KEEP_ALIVE = "30m"         # 요청마다 30분 동안 메모리에 붙잡아 둔다
```

데모 중에는 30분 넘게 쉴 일이 없어서 기본값으로 충분하다.
계속 올려두고 싶으면:

```bash
export LLM_KEEP_ALIVE=-1     # 내리지 않음
```

---

## 6. 다른 기계에 둘 때

Ollama는 기본적으로 `127.0.0.1` 에만 열려 있어서, 밖에서 부르려면 바인딩을 바꿔야 한다.

**Ollama 쪽** (리눅스)

```bash
sudo systemctl edit ollama
```

```ini
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
```

```bash
sudo systemctl restart ollama
```

**FastAPI 쪽**

```bash
export LLM_BASE_URL=http://192.168.0.10:11434
export LLM_TIMEOUT=10        # 네트워크 왕복이 더해지므로 조금 늘린다
```

> **인증이 없다.** 아무나 부를 수 있으므로 공인 IP에 그대로 열지 말고,
> 사설망이나 방화벽으로 막아둘 것.

---

## 7. 환경변수 정리

| 변수 | 기본값 | 설명 |
|---|---|---|
| `LLM_PROVIDER` | `ollama` | `ollama` / `openai` / `off` |
| `LLM_MODEL` | `exaone3.5:7.8b` | 모델 태그 |
| `LLM_BASE_URL` | `http://localhost:11434` | Ollama 주소 |
| `LLM_TIMEOUT` | `6` | 평소 응답 대기(초) |
| `LLM_WARMUP_TIMEOUT` | `120` | 예열 대기(초) |
| `LLM_KEEP_ALIVE` | `30m` | 모델 유지 시간. `-1` 이면 안 내림 |
| `LLM_VERIFY_NUMBER` | `1` | 방 번호 검증. `0` 이면 끔 |

**LLM을 아예 끄고 싶으면** (규칙 엔진만 쓰기):

```bash
export LLM_PROVIDER=off
```

---

## 8. 문제가 생기면

| 증상 | 원인과 조치 |
|---|---|
| `LLM 예열 실패 ... Connection refused` | Ollama가 안 떠 있다. `ollama serve` 또는 `systemctl start ollama` |
| `model 'exaone3.5:7.8b' not found` | 모델을 안 받았다. `ollama pull exaone3.5:7.8b` |
| 방 번호는 되는데 "변소"만 안 됨 | LLM이 안 붙어 규칙 엔진으로 도는 중. 서버 로그 확인 |
| 첫 요청만 느리거나 실패 | 예열이 안 됐다. `LLM_KEEP_ALIVE=-1` 로 두거나 서버 로그에서 예열 성공 확인 |
| 응답이 3초 넘게 걸림 | 모델이 크거나 CPU만 쓰는 중. `exaone3.5:2.4b` 로 낮춰볼 것 |
| `LLM이 목록에 없는 id를 냄` 로그 | 정상 동작. 지어낸 답을 걸러낸 것이라 사용자에게는 안 나간다 |
| 메모리 부족으로 서버가 느려짐 | 더 작은 모델로 바꾸거나 `LLM_PROVIDER=off` |

---

## 참고

- [Ollama 다운로드](https://ollama.com/download)
- [EXAONE 3.5 (Ollama 라이브러리)](https://ollama.com/library/exaone3.5)
- [EXAONE 3.5 논문](https://arxiv.org/abs/2412.04862)
- 매칭 로직 설명: `docs/음성_목적지_매칭.md`
