package org.mcsmtp.wayfinder.floorplan.repository;

import org.mcsmtp.wayfinder.floorplan.entity.Floorplan;
import org.springframework.data.jpa.repository.JpaRepository;

public interface FloorplanRepository extends JpaRepository<Floorplan, String> {
}
