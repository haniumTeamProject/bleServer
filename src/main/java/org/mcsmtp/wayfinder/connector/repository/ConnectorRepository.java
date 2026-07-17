package org.mcsmtp.wayfinder.connector.repository;

import org.mcsmtp.wayfinder.connector.entity.Connector;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface ConnectorRepository extends JpaRepository<Connector, String> {
    List<Connector> findByBuildingId(String buildingId);
}
