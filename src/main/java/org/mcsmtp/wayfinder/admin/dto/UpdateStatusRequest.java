package org.mcsmtp.wayfinder.admin.dto;

import lombok.Getter;
import lombok.Setter;

@Getter
@Setter
public class UpdateStatusRequest {
    // active | rejected
    private String status;
}
