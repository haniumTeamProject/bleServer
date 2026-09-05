-- 002 · 외래키 제약 추가
--
-- !! 한 번에 실행하지 말 것. STEP 순서대로 나눠 실행한다.
-- !! STEP 2 의 건수를 보고 STEP 3 에서 실행할 줄을 고른다. STEP 3 은 행을
--    실제로 지운다. 배포 서버는 고아 행 사정이 로컬과 다르므로 그쪽에서도
--    STEP 2 부터 다시 확인한다.
--
-- 삭제 규칙은 서비스 계층(_purge_floor · delete_building)과 같게 맞춘다.
--   건물 삭제 → 층 · 층에 딸린 것 · 연결자 전부 삭제
--   층 삭제   → 비콘 · 목적지 · 연결자 좌표 · 도면 · 마스크 · 경로노드 삭제
-- 그래서 전부 ON DELETE CASCADE, 관리자 승인자만 SET NULL 이다.
--
-- 같은 내용이 app/*/models.py 의 ForeignKey 선언에도 들어 있다. 새 DB 는
-- create_all 이 제약까지 만들고, 이미 있는 DB 는 이 파일로 맞춘다.

-- STEP 2. 고아 행 점검 ------------------------------------------------
-- 참조 대상이 사라진 행이 하나라도 있으면 STEP 4 의 제약 추가가 실패한다.

SELECT 'floors.building_id'              AS ref, COUNT(*) FROM floors f
  WHERE f.building_id IS NOT NULL
    AND NOT EXISTS (SELECT 1 FROM buildings b WHERE b.id = f.building_id)
UNION ALL
SELECT 'connectors.building_id', COUNT(*) FROM connectors c
  WHERE c.building_id IS NOT NULL
    AND NOT EXISTS (SELECT 1 FROM buildings b WHERE b.id = c.building_id)
UNION ALL
SELECT 'floorplans.floor_id', COUNT(*) FROM floorplans t
  WHERE NOT EXISTS (SELECT 1 FROM floors f WHERE f.id = t.floor_id)
UNION ALL
SELECT 'floor_masks.floor_id', COUNT(*) FROM floor_masks t
  WHERE NOT EXISTS (SELECT 1 FROM floors f WHERE f.id = t.floor_id)
UNION ALL
SELECT 'floor_path_nodes.floor_id', COUNT(*) FROM floor_path_nodes t
  WHERE NOT EXISTS (SELECT 1 FROM floors f WHERE f.id = t.floor_id)
UNION ALL
SELECT 'beacons.floor_id', COUNT(*) FROM beacons t
  WHERE t.floor_id IS NOT NULL
    AND NOT EXISTS (SELECT 1 FROM floors f WHERE f.id = t.floor_id)
UNION ALL
SELECT 'landmarks.floor_id', COUNT(*) FROM landmarks t
  WHERE t.floor_id IS NOT NULL
    AND NOT EXISTS (SELECT 1 FROM floors f WHERE f.id = t.floor_id)
UNION ALL
SELECT 'connector_positions.floor_id', COUNT(*) FROM connector_positions t
  WHERE NOT EXISTS (SELECT 1 FROM floors f WHERE f.id = t.floor_id)
UNION ALL
SELECT 'connector_positions.connector_id', COUNT(*) FROM connector_positions t
  WHERE NOT EXISTS (SELECT 1 FROM connectors c WHERE c.id = t.connector_id)
UNION ALL
SELECT 'admins.approved_by', COUNT(*) FROM admins a
  WHERE a.approved_by IS NOT NULL
    AND NOT EXISTS (SELECT 1 FROM admins x WHERE x.id = a.approved_by);


-- STEP 3. 고아 행 정리 ------------------------------------------------
-- STEP 2 에서 0 이 아닌 항목만 골라 실행한다. 지우기 전에 무엇이 지워지는지
-- SELECT 로 먼저 확인할 것. 특히 고아 층에는 비콘·목적지가 딸려 있다.
--
-- 순서가 중요하다 — 층에 딸린 것을 먼저 지우고 층을 지운다.

-- 고아 층에 딸린 것
DELETE FROM beacons             WHERE floor_id NOT IN (SELECT id FROM floors);
DELETE FROM landmarks           WHERE floor_id NOT IN (SELECT id FROM floors);
DELETE FROM connector_positions WHERE floor_id NOT IN (SELECT id FROM floors);
DELETE FROM floorplans          WHERE floor_id NOT IN (SELECT id FROM floors);
DELETE FROM floor_masks         WHERE floor_id NOT IN (SELECT id FROM floors);
DELETE FROM floor_path_nodes    WHERE floor_id NOT IN (SELECT id FROM floors);

-- 주인 없는 연결자 좌표
DELETE FROM connector_positions WHERE connector_id NOT IN (SELECT id FROM connectors);

-- 주인 없는 층 · 연결자 (딸린 것을 위에서 이미 지웠다)
DELETE FROM beacons             WHERE floor_id IN
  (SELECT id FROM floors WHERE building_id NOT IN (SELECT id FROM buildings));
DELETE FROM landmarks           WHERE floor_id IN
  (SELECT id FROM floors WHERE building_id NOT IN (SELECT id FROM buildings));
DELETE FROM connector_positions WHERE floor_id IN
  (SELECT id FROM floors WHERE building_id NOT IN (SELECT id FROM buildings));
DELETE FROM floorplans          WHERE floor_id IN
  (SELECT id FROM floors WHERE building_id NOT IN (SELECT id FROM buildings));
DELETE FROM floor_masks         WHERE floor_id IN
  (SELECT id FROM floors WHERE building_id NOT IN (SELECT id FROM buildings));
DELETE FROM floor_path_nodes    WHERE floor_id IN
  (SELECT id FROM floors WHERE building_id NOT IN (SELECT id FROM buildings));
DELETE FROM floors              WHERE building_id NOT IN (SELECT id FROM buildings);
DELETE FROM connectors          WHERE building_id NOT IN (SELECT id FROM buildings);

-- 사라진 승인자는 비운다 (행은 남긴다)
UPDATE admins SET approved_by = NULL
  WHERE approved_by IS NOT NULL
    AND approved_by NOT IN (SELECT id FROM admins);


-- STEP 4. 외래키 제약 추가 -------------------------------------------

ALTER TABLE floors
  ADD CONSTRAINT fk_floors_building
  FOREIGN KEY (building_id) REFERENCES buildings(id) ON DELETE CASCADE;

ALTER TABLE connectors
  ADD CONSTRAINT fk_connectors_building
  FOREIGN KEY (building_id) REFERENCES buildings(id) ON DELETE CASCADE;

ALTER TABLE floorplans
  ADD CONSTRAINT fk_floorplans_floor
  FOREIGN KEY (floor_id) REFERENCES floors(id) ON DELETE CASCADE;

ALTER TABLE floor_masks
  ADD CONSTRAINT fk_floor_masks_floor
  FOREIGN KEY (floor_id) REFERENCES floors(id) ON DELETE CASCADE;

ALTER TABLE floor_path_nodes
  ADD CONSTRAINT fk_floor_path_nodes_floor
  FOREIGN KEY (floor_id) REFERENCES floors(id) ON DELETE CASCADE;

ALTER TABLE beacons
  ADD CONSTRAINT fk_beacons_floor
  FOREIGN KEY (floor_id) REFERENCES floors(id) ON DELETE CASCADE;

ALTER TABLE landmarks
  ADD CONSTRAINT fk_landmarks_floor
  FOREIGN KEY (floor_id) REFERENCES floors(id) ON DELETE CASCADE;

ALTER TABLE connector_positions
  ADD CONSTRAINT fk_connector_positions_connector
  FOREIGN KEY (connector_id) REFERENCES connectors(id) ON DELETE CASCADE;

ALTER TABLE connector_positions
  ADD CONSTRAINT fk_connector_positions_floor
  FOREIGN KEY (floor_id) REFERENCES floors(id) ON DELETE CASCADE;

ALTER TABLE admins
  ADD CONSTRAINT fk_admins_approved_by
  FOREIGN KEY (approved_by) REFERENCES admins(id) ON DELETE SET NULL;


-- STEP 5. 확인 --------------------------------------------------------
-- 10 건이 나와야 한다.

SELECT conrelid::regclass AS "표", conname AS "제약",
       pg_get_constraintdef(oid) AS "정의"
  FROM pg_constraint
 WHERE contype = 'f'
   AND connamespace = 'public'::regnamespace
 ORDER BY 1, 2;


-- 되돌리기 (필요할 때만) ----------------------------------------------
-- ALTER TABLE floors              DROP CONSTRAINT fk_floors_building;
-- ALTER TABLE connectors          DROP CONSTRAINT fk_connectors_building;
-- ALTER TABLE floorplans          DROP CONSTRAINT fk_floorplans_floor;
-- ALTER TABLE floor_masks         DROP CONSTRAINT fk_floor_masks_floor;
-- ALTER TABLE floor_path_nodes    DROP CONSTRAINT fk_floor_path_nodes_floor;
-- ALTER TABLE beacons             DROP CONSTRAINT fk_beacons_floor;
-- ALTER TABLE landmarks           DROP CONSTRAINT fk_landmarks_floor;
-- ALTER TABLE connector_positions DROP CONSTRAINT fk_connector_positions_connector;
-- ALTER TABLE connector_positions DROP CONSTRAINT fk_connector_positions_floor;
-- ALTER TABLE admins              DROP CONSTRAINT fk_admins_approved_by;
