package org.mcsmtp.wayfinder.building.service;

import lombok.RequiredArgsConstructor;
import org.mcsmtp.wayfinder.building.dto.BuildingRequest;
import org.mcsmtp.wayfinder.building.entity.Building;
import org.mcsmtp.wayfinder.building.repository.BuildingRepository;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;

import java.util.List;

@Service
@RequiredArgsConstructor
public class BuildingService {

    private final BuildingRepository buildingRepository;

    public List<Building> findAll() {
        return buildingRepository.findAll();
    }

    public Building findById(String id) {
        return buildingRepository.findById(id)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "건물 없음: " + id));
    }

    public Building create(BuildingRequest req) {
        Building building = new Building();
        building.setCode(req.getCode());
        building.setName(req.getName());
        building.setAddress(req.getAddress());
        building.setFloorCount(req.getFloorCount());
        building.setStatus("floorplan_missing");
        return buildingRepository.save(building); // save() 호출 시 INSERT 실행
    }

    public Building update(String id, BuildingRequest req) {
        Building building = findById(id);
        if (req.getCode() != null) building.setCode(req.getCode());
        if (req.getName() != null) building.setName(req.getName());
        if (req.getAddress() != null) building.setAddress(req.getAddress());
        if (req.getFloorCount() != null) building.setFloorCount(req.getFloorCount());
        return buildingRepository.save(building); // id가 이미 있으면 INSERT 대신 UPDATE 실행
    }

    public void delete(String id) {
        buildingRepository.deleteById(id);
    }
}