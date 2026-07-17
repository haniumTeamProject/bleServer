package org.mcsmtp.wayfinder.mask.dto;

import lombok.Getter;
import lombok.Setter;

@Getter
@Setter
public class FloorMaskRequest {
    private Integer width;
    private Integer height;
    private String dataUrl;
}
