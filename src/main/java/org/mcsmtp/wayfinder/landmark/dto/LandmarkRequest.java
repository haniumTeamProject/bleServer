package org.mcsmtp.wayfinder.landmark.dto;

import lombok.Getter;
import lombok.Setter;

@Getter
@Setter
public class LandmarkRequest {
    private String name;
    private String type;
    private Double x;
    private Double y;
}
