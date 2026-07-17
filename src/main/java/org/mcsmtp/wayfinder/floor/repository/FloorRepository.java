package org.mcsmtp.wayfinder.floor.repository;

import org.mcsmtp.wayfinder.floor.entity.Floor;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface FloorRepository extends JpaRepository<Floor, String> {
    List<Floor> findByBuildingIdOrderByFloorAsc(String buildingId);
}
