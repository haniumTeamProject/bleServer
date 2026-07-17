package org.mcsmtp.wayfinder.landmark.entity;

import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.util.UUID;

@Entity
@Table(name = "landmarks")
@Getter
@Setter
@NoArgsConstructor
public class Landmark {

    @Id
    private String id = UUID.randomUUID().toString();

    private String floorId;

    // 사용자가 음성으로 말하는 목적지 이름
    private String name;

    // room | restroom | facility | entrance
    private String type;

    // 시각태그 연결 (선택)
    private String visualTagId;

    // 설계도 좌표 (900 기준)
    private Double x;
    private Double y;
}
