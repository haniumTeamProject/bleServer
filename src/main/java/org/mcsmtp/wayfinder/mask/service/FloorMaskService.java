package org.mcsmtp.wayfinder.mask.service;

import lombok.RequiredArgsConstructor;
import org.mcsmtp.wayfinder.floor.service.FloorService;
import org.mcsmtp.wayfinder.mask.dto.FloorMaskRequest;
import org.mcsmtp.wayfinder.mask.entity.FloorMask;
import org.mcsmtp.wayfinder.mask.repository.FloorMaskRepository;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class FloorMaskService {

    private final FloorMaskRepository floorMaskRepository;
    private final FloorService floorService;

    public FloorMask find(String floorId) {
        return floorMaskRepository.findById(floorId).orElse(null);
    }

    public FloorMask save(String floorId, FloorMaskRequest req) {
        FloorMask mask = floorMaskRepository.findById(floorId).orElseGet(FloorMask::new);
        mask.setFloorId(floorId);
        mask.setWidth(req.getWidth());
        mask.setHeight(req.getHeight());
        mask.setDataUrl(req.getDataUrl());
        FloorMask saved = floorMaskRepository.save(mask);

        floorService.bumpStatus(floorId, "review_needed", "beacon_missing");
        return saved;
    }
}
