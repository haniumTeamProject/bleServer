package org.mcsmtp.wayfinder.connector.controller;

import lombok.RequiredArgsConstructor;
import org.mcsmtp.wayfinder.connector.dto.ConnectorRequest;
import org.mcsmtp.wayfinder.connector.entity.Connector;
import org.mcsmtp.wayfinder.connector.service.ConnectorService;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/api/buildings/{buildingId}/connectors")
@RequiredArgsConstructor
public class ConnectorController {

    private final ConnectorService connectorService;

    @GetMapping
    public List<Connector> list(@PathVariable String buildingId) {
        return connectorService.findByBuilding(buildingId);
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public Connector create(@PathVariable String buildingId, @RequestBody ConnectorRequest req) {
        return connectorService.create(buildingId, req);
    }

    @DeleteMapping("/{connectorId}")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void delete(@PathVariable String buildingId, @PathVariable String connectorId) {
        connectorService.delete(connectorId);
    }
}
