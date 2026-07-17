package org.mcsmtp.wayfinder.floorplan.controller;

import lombok.RequiredArgsConstructor;
import org.mcsmtp.wayfinder.floorplan.dto.FloorplanRequest;
import org.mcsmtp.wayfinder.floorplan.entity.Floorplan;
import org.mcsmtp.wayfinder.floorplan.service.FloorplanService;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/floors/{floorId}/floorplan")
@RequiredArgsConstructor
public class FloorplanController {

    private final FloorplanService floorplanService;

    @GetMapping
    public Floorplan get(@PathVariable String floorId) {
        return floorplanService.find(floorId);
    }

    @PutMapping
    public Floorplan upload(@PathVariable String floorId, @RequestBody FloorplanRequest req) {
        return floorplanService.upload(floorId, req);
    }

    @DeleteMapping
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void delete(@PathVariable String floorId) {
        floorplanService.delete(floorId);
    }
}
