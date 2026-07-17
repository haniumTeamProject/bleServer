package org.mcsmtp.wayfinder.building.entity;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.util.UUID;

@Entity
@Table(name = "buildings")
@Getter
@Setter
@NoArgsConstructor
public class Building {

    @Id
    private String id = UUID.randomUUID().toString();

    private String code;
    private String name;
    private String address;
    private Integer floorCount;
    private Boolean favorite = false;

    // floorplan_missing | review_needed | beacon_missing | connector_missing | ready
    private String status = "floorplan_missing";
}