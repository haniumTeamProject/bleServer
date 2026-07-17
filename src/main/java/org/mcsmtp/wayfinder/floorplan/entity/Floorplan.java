package org.mcsmtp.wayfinder.floorplan.entity;

import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Lob;
import jakarta.persistence.Table;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

// 층 하나당 설계도 1개 (1:1) 라서 floorId를 그대로 PK로 사용
@Entity
@Table(name = "floorplans")
@Getter
@Setter
@NoArgsConstructor
public class Floorplan {

    @Id
    private String floorId;

    // 업로드된 이미지 data URL. 매우 길 수 있어 CLOB으로 저장
    @Lob
    private String imageUrl;

    private Boolean extracted = false;
}
