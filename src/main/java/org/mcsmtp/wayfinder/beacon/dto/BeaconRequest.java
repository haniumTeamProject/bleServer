package org.mcsmtp.wayfinder.beacon.dto;

import lombok.Getter;
import lombok.Setter;

// 생성/수정 요청 공용. 수정 시 null인 필드는 그대로 유지됨(Service에서 처리)
@Getter
@Setter
public class BeaconRequest {
    private String name;
    private String mac;
    private Integer minor;
    private String type;
    private String connectorId;
    private Double x;
    private Double y;
}
