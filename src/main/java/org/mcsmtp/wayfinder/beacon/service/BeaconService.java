package org.mcsmtp.wayfinder.beacon.service;

import lombok.RequiredArgsConstructor;
import org.mcsmtp.wayfinder.beacon.dto.BeaconRequest;
import org.mcsmtp.wayfinder.beacon.entity.Beacon;
import org.mcsmtp.wayfinder.beacon.repository.BeaconRepository;
import org.mcsmtp.wayfinder.floor.service.FloorService;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;

import java.util.List;

@Service
@RequiredArgsConstructor
public class BeaconService {

    private final BeaconRepository beaconRepository;
    private final FloorService floorService;

    public List<Beacon> findByFloor(String floorId) {
        return beaconRepository.findByFloorId(floorId);
    }

    public Beacon create(String floorId, BeaconRequest req) {
        Integer major = floorService.findById(floorId).getMajor();

        Beacon beacon = new Beacon();
        beacon.setFloorId(floorId);
        beacon.setName(req.getName());
        beacon.setMac(req.getMac());
        beacon.setMajor(major);
        beacon.setMinor(req.getMinor());
        beacon.setType(req.getType());
        beacon.setConnectorId(req.getConnectorId());
        beacon.setIsAnchor("anchor".equals(req.getType()));
        beacon.setX(req.getX());
        beacon.setY(req.getY());
        Beacon saved = beaconRepository.save(beacon);

        // 첫 비콘 등록 시 층 상태: beacon_missing -> ready
        floorService.bumpStatus(floorId, "beacon_missing", "ready");
        return saved;
    }

    public Beacon update(String beaconId, BeaconRequest req) {
        Beacon beacon = beaconRepository.findById(beaconId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "비콘 없음: " + beaconId));

        if (req.getName() != null) beacon.setName(req.getName());
        if (req.getMac() != null) beacon.setMac(req.getMac());
        if (req.getMinor() != null) beacon.setMinor(req.getMinor());
        if (req.getType() != null) beacon.setType(req.getType());
        if (req.getConnectorId() != null) beacon.setConnectorId(req.getConnectorId());
        if (req.getX() != null) beacon.setX(req.getX());
        if (req.getY() != null) beacon.setY(req.getY());
        beacon.setIsAnchor("anchor".equals(beacon.getType()));

        return beaconRepository.save(beacon);
    }

    public void delete(String beaconId) {
        beaconRepository.deleteById(beaconId);
    }
}
