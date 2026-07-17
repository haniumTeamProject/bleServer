package org.mcsmtp.wayfinder.landmark.service;

import lombok.RequiredArgsConstructor;
import org.mcsmtp.wayfinder.landmark.dto.LandmarkRequest;
import org.mcsmtp.wayfinder.landmark.entity.Landmark;
import org.mcsmtp.wayfinder.landmark.repository.LandmarkRepository;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;

import java.util.List;

@Service
@RequiredArgsConstructor
public class LandmarkService {

    private final LandmarkRepository landmarkRepository;

    public List<Landmark> findByFloor(String floorId) {
        return landmarkRepository.findByFloorId(floorId);
    }

    public Landmark create(String floorId, LandmarkRequest req) {
        Landmark landmark = new Landmark();
        landmark.setFloorId(floorId);
        landmark.setName(req.getName());
        landmark.setType(req.getType());
        landmark.setX(req.getX());
        landmark.setY(req.getY());
        return landmarkRepository.save(landmark);
    }

    public Landmark update(String landmarkId, LandmarkRequest req) {
        Landmark landmark = landmarkRepository.findById(landmarkId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "목적지 없음: " + landmarkId));

        if (req.getName() != null) landmark.setName(req.getName());
        if (req.getType() != null) landmark.setType(req.getType());
        if (req.getX() != null) landmark.setX(req.getX());
        if (req.getY() != null) landmark.setY(req.getY());

        return landmarkRepository.save(landmark);
    }

    public void delete(String landmarkId) {
        landmarkRepository.deleteById(landmarkId);
    }
}
