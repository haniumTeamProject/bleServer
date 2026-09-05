-- 001 · floor_path_nodes 컬럼 추가
--
-- 관리자가 경로노드 화면에서 정한 건너기 설정을 저장하는 칸이다. 모델에는
-- 있으나 실 DB 에는 없다 — create_all 은 표를 만들 뿐 컬럼을 더하지 않는다.
-- 몇 번 실행해도 안전하다.

ALTER TABLE floor_path_nodes ADD COLUMN IF NOT EXISTS cross_penalty_m DOUBLE PRECISION;
ALTER TABLE floor_path_nodes ADD COLUMN IF NOT EXISTS crossing_max_m  DOUBLE PRECISION;
