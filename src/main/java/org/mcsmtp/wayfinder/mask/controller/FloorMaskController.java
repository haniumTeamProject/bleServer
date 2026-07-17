package org.mcsmtp.wayfinder.mask.controller;

import lombok.RequiredArgsConstructor;
import org.mcsmtp.wayfinder.mask.dto.FloorMaskRequest;
import org.mcsmtp.wayfinder.mask.entity.FloorMask;
import org.mcsmtp.wayfinder.mask.service.FloorMaskService;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

@RestController
@RequestMapping("/api/floors/{floorId}/mask")
@RequiredArgsConstructor
public class FloorMaskController {

    private final FloorMaskService floorMaskService;

    @GetMapping
    public FloorMask get(@PathVariable String floorId) {
        return floorMaskService.find(floorId);
    }

    @PutMapping
    public Map<String, Boolean> save(@PathVariable String floorId, @RequestBody FloorMaskRequest req) {
        floorMaskService.save(floorId, req);
        return Map.of("ok", true);
    }
}
