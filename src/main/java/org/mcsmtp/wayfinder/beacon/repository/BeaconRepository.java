package org.mcsmtp.wayfinder.beacon.repository;

import org.mcsmtp.wayfinder.beacon.entity.Beacon;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface BeaconRepository extends JpaRepository<Beacon, String> {
    List<Beacon> findByFloorId(String floorId);
}
