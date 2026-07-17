package org.mcsmtp.wayfinder.beacon.controller;

import lombok.RequiredArgsConstructor;
import org.mcsmtp.wayfinder.beacon.dto.BeaconRequest;
import org.mcsmtp.wayfinder.beacon.entity.Beacon;
import org.mcsmtp.wayfinder.beacon.service.BeaconService;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/api/floors/{floorId}/beacons")
@RequiredArgsConstructor
public class BeaconController {

    private final BeaconService beaconService;

    @GetMapping
    public List<Beacon> list(@PathVariable String floorId) {
        return beaconService.findByFloor(floorId);
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public Beacon create(@PathVariable String floorId, @RequestBody BeaconRequest req) {
        return beaconService.create(floorId, req);
    }

    @PatchMapping("/{beaconId}")
    public Beacon update(@PathVariable String floorId, @PathVariable String beaconId, @RequestBody BeaconRequest req) {
        return beaconService.update(beaconId, req);
    }

    @DeleteMapping("/{beaconId}")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void delete(@PathVariable String floorId, @PathVariable String beaconId) {
        beaconService.delete(beaconId);
    }
}
