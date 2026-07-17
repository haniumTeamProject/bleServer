package org.mcsmtp.wayfinder.landmark.repository;

import org.mcsmtp.wayfinder.landmark.entity.Landmark;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface LandmarkRepository extends JpaRepository<Landmark, String> {
    List<Landmark> findByFloorId(String floorId);
}
