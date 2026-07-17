package org.mcsmtp.wayfinder.building.controller;

import lombok.RequiredArgsConstructor;
import org.mcsmtp.wayfinder.building.dto.BuildingRequest;
import org.mcsmtp.wayfinder.building.entity.Building;
import org.mcsmtp.wayfinder.building.service.BuildingService;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/api/buildings")
@RequiredArgsConstructor
public class BuildingController {

    private final BuildingService buildingService;

    @GetMapping
    public List<Building> list() {
        return buildingService.findAll();
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public Building create(@RequestBody BuildingRequest req) {
        return buildingService.create(req);
    }

    @GetMapping("/{id}")
    public Building get(@PathVariable String id) {
        return buildingService.findById(id);
    }

    @PatchMapping("/{id}")
    public Building update(@PathVariable String id, @RequestBody BuildingRequest req) {
        return buildingService.update(id, req);
    }

    @DeleteMapping("/{id}")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void delete(@PathVariable String id) {
        buildingService.delete(id);
    }
}