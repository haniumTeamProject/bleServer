package org.mcsmtp.wayfinder.floor.service;

import lombok.RequiredArgsConstructor;
import org.mcsmtp.wayfinder.floor.dto.FloorRequest;
import org.mcsmtp.wayfinder.floor.entity.Floor;
import org.mcsmtp.wayfinder.floor.repository.FloorRepository;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;

import java.util.List;

@Service
@RequiredArgsConstructor
public class FloorService {

    private final FloorRepository floorRepository;

    public List<Floor> findByBuilding(String buildingId) {
        return floorRepository.findByBuildingIdOrderByFloorAsc(buildingId);
    }

    public Floor findById(String id) {
        return floorRepository.findById(id)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "층 없음: " + id));
    }

    public Floor create(String buildingId, FloorRequest req) {
        Floor floor = new Floor();
        floor.setBuildingId(buildingId);
        floor.setFloor(req.getFloor());
        floor.setMajor(100 + req.getFloor());
        floor.setStatus("floorplan_missing");
        return floorRepository.save(floor);
    }

    public void delete(String floorId) {
        floorRepository.deleteById(floorId);
    }

    // 설계도/마스크/비콘 도메인에서 이 층의 세팅 상태를 진행시킬 때 호출
    // from 상태일 때만 to로 바뀜 (mock의 bumpFloorStatus와 동일한 idempotent 가드)
    public void bumpStatus(String floorId, String from, String to) {
        floorRepository.findById(floorId).ifPresent(floor -> {
            if (from.equals(floor.getStatus())) {
                floor.setStatus(to);
                floorRepository.save(floor);
            }
        });
    }
}
