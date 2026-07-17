package org.mcsmtp.wayfinder.connector.service;

import lombok.RequiredArgsConstructor;
import org.mcsmtp.wayfinder.connector.dto.ConnectorRequest;
import org.mcsmtp.wayfinder.connector.entity.Connector;
import org.mcsmtp.wayfinder.connector.repository.ConnectorRepository;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

@Service
@RequiredArgsConstructor
public class ConnectorService {

    private final ConnectorRepository connectorRepository;

    public List<Connector> findByBuilding(String buildingId) {
        return connectorRepository.findByBuildingId(buildingId);
    }

    public Connector create(String buildingId, ConnectorRequest req) {
        Connector connector = new Connector();
        connector.setBuildingId(buildingId);
        connector.setName(req.getName());
        connector.setType(req.getType());

        List<Integer> floors = new ArrayList<>(req.getFloors() != null ? req.getFloors() : List.of());
        Collections.sort(floors);
        connector.setFloors(floors);

        return connectorRepository.save(connector);
    }

    public void delete(String connectorId) {
        connectorRepository.deleteById(connectorId);
    }
}
