package org.mcsmtp.wayfinder.landmark.controller;

import lombok.RequiredArgsConstructor;
import org.mcsmtp.wayfinder.landmark.dto.LandmarkRequest;
import org.mcsmtp.wayfinder.landmark.entity.Landmark;
import org.mcsmtp.wayfinder.landmark.service.LandmarkService;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/api/floors/{floorId}/landmarks")
@RequiredArgsConstructor
public class LandmarkController {

    private final LandmarkService landmarkService;

    @GetMapping
    public List<Landmark> list(@PathVariable String floorId) {
        return landmarkService.findByFloor(floorId);
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public Landmark create(@PathVariable String floorId, @RequestBody LandmarkRequest req) {
        return landmarkService.create(floorId, req);
    }

    @PatchMapping("/{landmarkId}")
    public Landmark update(@PathVariable String floorId, @PathVariable String landmarkId, @RequestBody LandmarkRequest req) {
        return landmarkService.update(landmarkId, req);
    }

    @DeleteMapping("/{landmarkId}")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void delete(@PathVariable String floorId, @PathVariable String landmarkId) {
        landmarkService.delete(landmarkId);
    }
}
