# 지금까지 작업을 커밋한다. 푸시는 하지 않는다.
#
#   .\commit.ps1
#
# 앞 단계 커밋은 뒤 단계 파일을 아직 안 갖고 있어서 그 시점에서는 서버가 안 뜬다
# (main.py 가 마지막에 들어간다). 브랜치 끝에서만 돌아가면 되는 작업 브랜치라 그대로 둔다.

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

if (Test-Path .git\index.lock) { Remove-Item .git\index.lock -Force }

if (-not (git config user.name))  { git config user.name  "안준성" }
if (-not (git config user.email)) { git config user.email "taskcomminution@gmail.com" }

function Commit($msg, $paths) {
    git add -- $paths
    git commit -m $msg
}

Commit "음성으로 말한 목적지를 랜드마크에 잇는 매칭 엔진 추가" @(
    "app/ws/landmark_matcher.py", "app/ws/llm_matcher.py", "app/ws/handler.py",
    "tests/test_landmark_matcher.py", "tests/test_llm_matcher.py",
    "tests/check_llm.py", "tests/try_destination.py",
    "docs/음성_목적지_매칭.md", "docs/Ollama_설치.md",
    "docs/로컬AI_따라하기.md", "docs/목적지_테스트하기.md"
)

Commit "경로 탐색 엔진과 지도 데이터 공급자 추가" @(
    "app/nav/__init__.py", "app/nav/map_source.py", "app/nav/route_engine.py",
    "tests/test_route_engine.py"
)

Commit "교차 시 신호차 판정 조건 추가, 실측 데이터셋 편입" @(
    "app/ws/path_tracker.py", "tests/eval_tracker.py",
    "tests/measurements", "docs/교차_신호차_판정.md",
    "docs/위치판정_코드해설.md", "docs/2단계_확인_판정.md"
)

Commit "WEB-FE 접합: 스키마·엔드포인트 정렬, 층 상태를 조회 시 계산으로 변경" @(
    "app/admin", "app/beacon", "app/building", "app/connector",
    "app/floor", "app/floorplan", "app/landmark", "app/mask", "app/status.py",
    "tests/test_admin_api.py", "tests/test_webfe_contract.py",
    "docs/WEBFE_접합_변경기록.md", "docs/API명세_최종.md", "docs/개발환경_준비.md",
    "docs/WEBFE_백엔드_연결_수정안.md"
)

Commit "사용자앱 WS 규약 문서, 점검 스크립트 추가" @(
    "docs/API_목적지_되묻기_WS.md", "docs/사용자앱_API_명세.md",
    "tests/check_pipeline.py", "tests/ws_phone.py"
)

Commit "/monitor 지도가 DB를 읽는 피드 추가, 실측 프로젝트 시드 스크립트" @(
    "app/nav/db_map_source.py", "app/nav/router.py", "app/main.py",
    "tests/test_map_db.py", "tests/seed_from_project.py"
)

Commit "경로노드 생성기를 파이썬으로 이식, 건너기를 양방향으로 변경" @(
    "app/nav/path_nodes.py", "app/nav/route_engine.py", "app/nav/db_map_source.py",
    "tests/test_path_nodes.py", "tests/test_route_from_db.py",
    "tests/fixtures", "tests/__init__.py", "requirements.txt"
)

Write-Host ""
git log --oneline -6
Write-Host ""
git status --porcelain
Write-Host "(위가 비어 있으면 전부 커밋됨. 푸시는 안 했다)"
