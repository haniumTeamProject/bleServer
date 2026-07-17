package org.mcsmtp.wayfinder.beacon.entity;

import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.util.UUID;

@Entity
@Table(name = "beacons")
@Getter
@Setter
@NoArgsConstructor
public class Beacon {

    @Id
    private String id = UUID.randomUUID().toString();

    private String floorId;
    private String name;
    private String mac;

    // 소속 층의 major 값을 그대로 복사 저장 (서버가 계산)
    private Integer major;
    private Integer minor;

    // anchor | checkpoint | connector
    private String type;

    // 엘베/계단(connector) 타입일 때만 사용
    private String connectorId;

    // type == "anchor" 여부 (서버가 계산)
    private Boolean isAnchor = false;

    // 설계도 좌표 (900 기준)
    private Double x;
    private Double y;
}
