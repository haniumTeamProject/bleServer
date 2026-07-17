package org.mcsmtp.wayfinder.floor.controller;

import lombok.RequiredArgsConstructor;
import org.mcsmtp.wayfinder.floor.dto.FloorRequest;
import org.mcsmtp.wayfinder.floor.entity.Floor;
import org.mcsmtp.wayfinder.floor.service.FloorService;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/api/buildings/{buildingId}/floors")
@RequiredArgsConstructor
public class FloorController {

    private final FloorService floorService;

    @GetMapping
    public List<Floor> list(@PathVariable String buildingId) {
        return floorService.findByBuilding(buildingId);
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public Floor create(@PathVariable String buildingId, @RequestBody FloorRequest req) {
        return floorService.create(buildingId, req);
    }

    @DeleteMapping("/{floorId}")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void delete(@PathVariable String buildingId, @PathVariable String floorId) {
        floorService.delete(floorId);
    }
}
