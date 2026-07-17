package org.mcsmtp.wayfinder.connector.entity;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

@Entity
@Table(name = "connectors")
@Getter
@Setter
@NoArgsConstructor
public class Connector {

    @Id
    private String id = UUID.randomUUID().toString();

    private String buildingId;
    private String name;

    // elevator | stairs
    private String type;

    // 운행 층 목록. 별도 테이블(connector_floors)에 저장됨
    @ElementCollection
    @CollectionTable(name = "connector_floors", joinColumns = @JoinColumn(name = "connector_id"))
    @Column(name = "floor_num")
    private List<Integer> floors = new ArrayList<>();
}
