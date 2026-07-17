package org.mcsmtp.wayfinder.mask.entity;

import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Lob;
import jakarta.persistence.Table;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

// 층 하나당 이동영역 마스크 1개 (1:1) 라서 floorId를 그대로 PK로 사용
@Entity
@Table(name = "floor_masks")
@Getter
@Setter
@NoArgsConstructor
public class FloorMask {

    @Id
    private String floorId;

    private Integer width;
    private Integer height;

    // 채워진 영역을 담은 투명배경 PNG data URL. 매우 길 수 있어 CLOB으로 저장
    @Lob
    private String dataUrl;
}
