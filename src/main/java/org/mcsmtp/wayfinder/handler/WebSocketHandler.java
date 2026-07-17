package org.mcsmtp.wayfinder.handler;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.mcsmtp.wayfinder.filter.RssiFilterPipeline;
import org.springframework.web.socket.CloseStatus;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;
import org.springframework.web.socket.handler.TextWebSocketHandler;

import java.util.HashMap;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CopyOnWriteArraySet;

public class WebSocketHandler extends TextWebSocketHandler {

    private final Set<WebSocketSession> sessions = new CopyOnWriteArraySet<>();
    private final ObjectMapper objectMapper = new ObjectMapper();
    private final Map<String, RssiFilterPipeline> filters = new HashMap<>();

    @Override
    public void afterConnectionEstablished(WebSocketSession session) throws Exception {
        sessions.add(session);
        System.out.println("Connected: " + session.getId());
    }

    @Override
    protected void handleTextMessage(WebSocketSession session, TextMessage message) throws Exception {
        String json = message.getPayload();

        try {
            JsonNode root = objectMapper.readTree(json);
            ObjectNode filtered = objectMapper.createObjectNode();

            if (root.has("timestamp")) {
                filtered.set("timestamp", root.get("timestamp"));
            } else {
                filtered.put("timestamp", System.currentTimeMillis());
            }

            root.fields().forEachRemaining(entry -> {
                String key = entry.getKey();
                if (key.equals("timestamp")) return;

                double rssi = entry.getValue().asDouble();
                if (rssi >= 0 || rssi == 127) return;

                filters.putIfAbsent(key, new RssiFilterPipeline());
                double filteredRssi = filters.get(key).filter(rssi);
                RssiFilterPipeline.BeaconState state = filters.get(key).getState();

                double rounded = Math.round(filteredRssi * 10.0) / 10.0;

                filtered.put(key, rssi);              // 원본값
                filtered.put(key + "__f", rounded);   // 칼만 필터값

                System.out.printf("비콘 %s | 원본: %.1f | 필터: %.1f | 상태: %s%n",
                    key, rssi, rounded, state);
            });

            String filteredJson = objectMapper.writeValueAsString(filtered);
            for (WebSocketSession s : sessions) {
                if (s.isOpen() && s != session) {
                    s.sendMessage(new TextMessage(filteredJson));
                }
            }

        } catch (Exception e) {
            System.err.println("필터 오류, 원본 전송: " + e.getMessage());
            for (WebSocketSession s : sessions) {
                if (s.isOpen() && s != session) {
                    s.sendMessage(new TextMessage(json));
                }
            }
        }
    }

    @Override
    public void afterConnectionClosed(WebSocketSession session, CloseStatus status) throws Exception {
        sessions.remove(session);
        System.out.println("Disconnected: " + session.getId());
    }
}