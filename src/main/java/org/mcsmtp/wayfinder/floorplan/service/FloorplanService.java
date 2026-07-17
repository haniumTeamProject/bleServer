package org.mcsmtp.wayfinder.floorplan.service;

import lombok.RequiredArgsConstructor;
import org.mcsmtp.wayfinder.floor.service.FloorService;
import org.mcsmtp.wayfinder.floorplan.dto.FloorplanRequest;
import org.mcsmtp.wayfinder.floorplan.entity.Floorplan;
import org.mcsmtp.wayfinder.floorplan.repository.FloorplanRepository;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class FloorplanService {

    private final FloorplanRepository floorplanRepository;
    private final FloorService floorService;

    public Floorplan find(String floorId) {
        return floorplanRepository.findById(floorId).orElse(null);
    }

    public Floorplan upload(String floorId, FloorplanRequest req) {
        // TODO: 실제 서버는 여기서 벽·이동영역을 자동 추출해야 함 (이미지 분석 로직).
        // 우선은 mock처럼 즉시 완료 처리.
        Floorplan floorplan = floorplanRepository.findById(floorId).orElseGet(Floorplan::new);
        floorplan.setFloorId(floorId);
        floorplan.setImageUrl(req.getImageUrl());
        floorplan.setExtracted(true);
        Floorplan saved = floorplanRepository.save(floorplan);

        floorService.bumpStatus(floorId, "floorplan_missing", "review_needed");
        return saved;
    }

    public void delete(String floorId) {
        floorplanRepository.deleteById(floorId);
    }
}
