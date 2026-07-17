package org.mcsmtp.wayfinder.building.dto;

import lombok.Getter;
import lombok.Setter;

@Getter
@Setter
public class BuildingRequest {
    private String code;
    private String name;
    private String address;
    private Integer floorCount;
}