package org.mcsmtp.wayfinder.floor.entity;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.util.UUID;

@Entity
@Table(name = "floors")
@Getter
@Setter
@NoArgsConstructor
public class Floor {

    @Id
    private String id = UUID.randomUUID().toString();

    private String buildingId;
    private Integer floor;
    private Integer major;

    // floorplan_missing | review_needed | beacon_missing | connector_missing | ready
    private String status = "floorplan_missing";
}
