package org.mcsmtp.wayfinder.connector.dto;

import lombok.Getter;
import lombok.Setter;

import java.util.List;

@Getter
@Setter
public class ConnectorRequest {
    private String name;
    private String type;
    private List<Integer> floors;
}
