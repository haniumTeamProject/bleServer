# Wayfinder 백엔드 구현 가이드 — DB 연동 + 전체 API 명세

REST API(웹 관리자 프론트엔드용) + 기존 WebSocket(`/ws`, 실시간 비콘 위치추정)을 하나의 Spring Boot 앱으로 묶는 걸 전제로 작성했습니다. DB 사용법 위주로 설명하고, 프론트엔드가 실제로 호출하는 모든 엔드포인트를 빠짐없이 정리했습니다.

---

## 1. DB 연동 준비

### 1-1. 의존성 추가 (`build.gradle`)

지금 `build.gradle`에는 DB 관련 의존성이 전혀 없습니다. 아래 두 줄을 `dependencies { }` 안에 추가하세요.

```groovy
dependencies {
    implementation 'org.springframework.boot:spring-boot-starter-thymeleaf'
    implementation 'org.springframework.boot:spring-boot-starter-webmvc'
    implementation 'org.springframework.boot:spring-boot-starter-websocket'
    implementation 'com.fasterxml.jackson.core:jackson-databind'
    compileOnly 'org.projectlombok:lombok'
    annotationProcessor 'org.projectlombok:lombok'

    // ↓ 추가
    implementation 'org.springframework.boot:spring-boot-starter-data-jpa'
    runtimeOnly 'com.h2database:h2'   // 개발용 파일 DB (설치 불필요)

    testImplementation 'org.springframework.boot:spring-boot-starter-test'
    testCompileOnly 'org.projectlombok:lombok'
    testRuntimeOnly 'org.junit.platform:junit-platform-launcher'
    testAnnotationProcessor 'org.projectlombok:lombok'
}
```

`spring-boot-starter-data-jpa`가 핵심입니다. 이게 있으면 "테이블을 SQL로 직접 안 만들어도, 자바 클래스(Entity)만 정의하면 Spring이 테이블을 알아서 만들고 CRUD 메서드도 자동으로 만들어주는" JPA/Hibernate가 활성화됩니다.

H2는 별도 설치 없이 파일 하나로 동작하는 DB라 개발 단계에 딱 좋습니다. 나중에 실서버에 올릴 때 MySQL이나 PostgreSQL로 바꾸는 건 의존성 한 줄 + 설정 몇 줄만 바꾸면 됩니다 (엔티티/레포지토리 코드는 그대로 재사용).

### 1-2. DB 설정 (`src/main/resources/application.properties`)

```properties
spring.application.name=wayfinder

# H2 파일 DB: ./data/wayfinder.mv.db 파일에 저장됨 (서버 재시작해도 데이터 유지)
spring.datasource.url=jdbc:h2:file:./data/wayfinder;AUTO_SERVER=TRUE
spring.datasource.driver-class-name=org.h2.Driver
spring.datasource.username=sa
spring.datasource.password=

# 엔티티 클래스 기준으로 테이블을 자동 생성/갱신 (개발용)
spring.jpa.hibernate.ddl-auto=update
spring.jpa.show-sql=true
spring.jpa.properties.hibernate.format_sql=true

# 브라우저에서 DB 내용을 직접 볼 수 있는 콘솔 (http://localhost:8080/h2-console)
spring.h2.console.enabled=true
spring.h2.console.path=/h2-console
```

`ddl-auto=update`는 "엔티티 클래스를 보고 테이블이 없으면 만들고, 컬럼이 추가됐으면 컬럼도 추가"해주는 옵션입니다. 개발 중엔 이걸로 편하게 쓰다가, 나중에 데이터가 실제로 쌓이는 운영 단계에서는 `validate`로 바꾸고 Flyway 같은 마이그레이션 도구로 스키마를 관리하는 게 일반적입니다. 지금 단계에선 신경 안 쓰셔도 됩니다.

실행 후 `http://localhost:8080/h2-console`에 접속해서 JDBC URL에 위 `spring.datasource.url` 값을 그대로 넣으면 테이블/데이터를 눈으로 직접 확인할 수 있습니다. 이게 있으면 "DB에 진짜 저장이 되고 있나?" 디버깅이 훨씬 쉬워집니다.

### 1-3. 나중에 MySQL로 바꾸고 싶을 때

```groovy
runtimeOnly 'com.mysql:mysql-connector-j'
```
```properties
spring.datasource.url=jdbc:mysql://localhost:3306/wayfinder
spring.datasource.driver-class-name=com.mysql.cj.jdbc.Driver
spring.datasource.username=root
spring.datasource.password=your_password
spring.jpa.database-platform=org.hibernate.dialect.MySQLDialect
```
이 부분만 바꾸면 되고, 자바 코드(Entity/Repository/Service/Controller)는 한 줄도 안 바꿔도 됩니다. 이게 JPA를 쓰는 가장 큰 이유입니다 — DB 종류에 상관없이 같은 코드로 동작.

---

## 2. Spring Data JPA 핵심 구조 (4개 레이어)

DB를 다루는 코드는 보통 4개 레이어로 나눕니다. 처음엔 이 흐름만 이해하면 됩니다.

| 레이어 | 역할 | 어노테이션 |
|---|---|---|
| **Entity** | DB 테이블 하나에 대응하는 자바 클래스 | `@Entity` |
| **Repository** | 그 테이블에 대한 CRUD — 인터페이스만 선언하면 Spring이 구현체를 자동 생성 | `extends JpaRepository<Entity타입, ID타입>` |
| **Service** | 실제 비즈니스 로직(상태 전이, 유효성 검사 등)이 들어가는 곳 | `@Service` |
| **Controller** | HTTP 요청을 받아서 Service를 호출하고 JSON으로 응답 | `@RestController` |

즉 요청이 들어오면 `Controller → Service → Repository → DB` 순서로 내려가고, 응답은 반대로 올라옵니다.

### 설계 원칙 (중요)

프론트엔드 타입(`domain.ts`)을 보면 `Floor.buildingId: string`, `Beacon.floorId: string`처럼 관계를 **문자열 ID**로 표현합니다. JPA의 `@ManyToOne`/`@OneToMany` 객체 참조를 쓰면 편리하긴 하지만, 초보 단계에서는 JSON 직렬화 시 무한 순환 참조(Building → Floor → Building → …) 문제가 자주 터집니다.

그래서 이 가이드에서는 **관계를 객체 참조 대신 순수 문자열 컬럼(`buildingId`, `floorId` 등)으로 저장**하는 방식을 씁니다. 프론트가 기대하는 JSON 모양과 정확히 일치하고, 훨씬 단순합니다. 조회할 때는 `findByBuildingId(String buildingId)` 같은 메서드를 Repository에 선언만 하면 Spring이 알아서 SQL을 만들어줍니다 (메서드 이름 기반 쿼리 생성 — JPA의 대표 기능).

ID는 전부 문자열 UUID로 생성합니다 (프론트 타입이 `id: string`이기 때문).

---

## 3. 전체 구현 예제 — 건물(Building) 도메인

이 패턴을 그대로 복제해서 나머지 도메인에 적용하면 됩니다.

### 3-1. Entity

```java
package org.mcsmtp.wayfinder.building.entity;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.util.UUID;

@Entity
@Table(name = "buildings")
@Getter
@Setter
@NoArgsConstructor
public class Building {

    @Id
    private String id = UUID.randomUUID().toString();

    private String code;
    private String name;
    private String address;
    private Integer floorCount;
    private Boolean favorite = false;

    // floorplan_missing | review_needed | beacon_missing | connector_missing | ready
    private String status = "floorplan_missing";
}
```

### 3-2. Repository

```java
package org.mcsmtp.wayfinder.building.repository;

import org.mcsmtp.wayfinder.building.entity.Building;
import org.springframework.data.jpa.repository.JpaRepository;

public interface BuildingRepository extends JpaRepository<Building, String> {
    // findAll(), findById(), save(), deleteById() 는 JpaRepository가 이미 제공
    // 추가로 필요한 조회는 메서드 이름만 선언하면 됨. 예:
    // List<Building> findByFavoriteTrue();
}
```

### 3-3. 요청 DTO (Data Transfer Object)

Entity를 그대로 요청 바디로 받으면 `id`, `status` 같은 서버가 관리해야 할 필드까지 클라이언트가 마음대로 채울 수 있어서 위험합니다. 요청 전용 클래스를 따로 둡니다.

```java
package org.mcsmtp.wayfinder.building.dto;

import lombok.Getter;
import lombok.Setter;

@Getter
@Setter
public class BuildingRequest {
    private String code;
    private String name;
    private String address;
    private Integer floorCount;
}
```

### 3-4. Service

```java
package org.mcsmtp.wayfinder.building.service;

import lombok.RequiredArgsConstructor;
import org.mcsmtp.wayfinder.building.dto.BuildingRequest;
import org.mcsmtp.wayfinder.building.entity.Building;
import org.mcsmtp.wayfinder.building.repository.BuildingRepository;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;

import java.util.List;

@Service
@RequiredArgsConstructor
public class BuildingService {

    private final BuildingRepository buildingRepository;

    public List<Building> findAll() {
        return buildingRepository.findAll();
    }

    public Building findById(String id) {
        return buildingRepository.findById(id)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "건물 없음: " + id));
    }

    public Building create(BuildingRequest req) {
        Building building = new Building();
        building.setCode(req.getCode());
        building.setName(req.getName());
        building.setAddress(req.getAddress());
        building.setFloorCount(req.getFloorCount());
        building.setStatus("floorplan_missing");
        return buildingRepository.save(building); // save() 호출 시 INSERT 실행
    }

    public Building update(String id, BuildingRequest req) {
        Building building = findById(id);
        if (req.getCode() != null) building.setCode(req.getCode());
        if (req.getName() != null) building.setName(req.getName());
        if (req.getAddress() != null) building.setAddress(req.getAddress());
        if (req.getFloorCount() != null) building.setFloorCount(req.getFloorCount());
        return buildingRepository.save(building); // id가 이미 있으면 INSERT 대신 UPDATE 실행
    }

    public void delete(String id) {
        buildingRepository.deleteById(id);
    }
}
```

### 3-5. Controller

```java
package org.mcsmtp.wayfinder.building.controller;

import lombok.RequiredArgsConstructor;
import org.mcsmtp.wayfinder.building.dto.BuildingRequest;
import org.mcsmtp.wayfinder.building.entity.Building;
import org.mcsmtp.wayfinder.building.service.BuildingService;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/api/buildings")
@RequiredArgsConstructor
public class BuildingController {

    private final BuildingService buildingService;

    @GetMapping
    public List<Building> list() {
        return buildingService.findAll();
    }

    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public Building create(@RequestBody BuildingRequest req) {
        return buildingService.create(req);
    }

    @GetMapping("/{id}")
    public Building get(@PathVariable String id) {
        return buildingService.findById(id);
    }

    @PatchMapping("/{id}")
    public Building update(@PathVariable String id, @RequestBody BuildingRequest req) {
        return buildingService.update(id, req);
    }

    @DeleteMapping("/{id}")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void delete(@PathVariable String id) {
        buildingService.delete(id);
    }
}
```

이 5개 파일이 "DB를 쓰는 법"의 전부입니다. `buildingRepository.save(building)`처럼 자바 객체를 넘기기만 하면 SQL `INSERT`/`UPDATE`가 자동으로 나갑니다. SQL을 직접 쓸 필요가 없습니다 (필요하면 `@Query` 어노테이션으로 직접 쓸 수도 있지만, 지금 수준에선 불필요).

### 3-6. CORS 설정 (프론트 개발 서버 연결용)

```java
package org.mcsmtp.wayfinder.config;

import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.CorsRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

@Configuration
public class WebConfig implements WebMvcConfigurer {
    @Override
    public void addCorsMappings(CorsRegistry registry) {
        registry.addMapping("/api/**")
                .allowedOrigins("http://localhost:5173")
                .allowedMethods("GET", "POST", "PATCH", "PUT", "DELETE")
                .allowedHeaders("*");
    }
}
```

프론트의 `.env`에서 `VITE_API_BASE_URL=http://localhost:8080/api`로 맞추면 이제 실제 이 서버와 통신합니다.

---

## 4. 나머지 7개 도메인 — 엔티티 설계표

Building과 똑같은 패턴(Entity + Repository + DTO + Service + Controller)으로 만드시면 됩니다. 각 엔티티에 들어갈 필드만 정리합니다.

**Floor**
| 필드 | 타입 | 비고 |
|---|---|---|
| id | String (UUID) | PK |
| buildingId | String | FK (객체 참조 대신 문자열로) |
| floor | Integer | 층수 |
| major | Integer | `100 + floor` 로 서버가 계산 |
| status | String | floorplan_missing 등 5종 |

**Connector**
| 필드 | 타입 | 비고 |
|---|---|---|
| id | String | PK |
| buildingId | String | FK |
| name | String | |
| type | String | `elevator` \| `stairs` |
| floors | `List<Integer>` | `@ElementCollection` 사용 (아래 참고) |

`floors`처럼 리스트 자체를 컬럼으로 저장하려면:
```java
@ElementCollection
@CollectionTable(name = "connector_floors", joinColumns = @JoinColumn(name = "connector_id"))
@Column(name = "floor_num")
private List<Integer> floors = new ArrayList<>();
```

**Floorplan**
| 필드 | 타입 | 비고 |
|---|---|---|
| floorId | String | PK로 사용 (층 1개당 설계도 1개) |
| imageUrl | String | data URL이 매우 길 수 있어 `@Column(columnDefinition = "CLOB")` 또는 TEXT 타입 지정 필요 |
| extracted | Boolean | |

**FloorMask** (이동영역 마스크)
| 필드 | 타입 | 비고 |
|---|---|---|
| floorId | String | PK |
| width | Integer | |
| height | Integer | |
| dataUrl | String | 위와 동일하게 큰 텍스트 컬럼 필요 |

**Beacon**
| 필드 | 타입 | 비고 |
|---|---|---|
| id | String | PK |
| floorId | String | FK |
| name | String | |
| mac | String | nullable |
| major | Integer | 소속 층의 major 값을 그대로 복사 저장 |
| minor | Integer | |
| type | String | `anchor` \| `checkpoint` \| `connector` |
| connectorId | String | nullable, 엘베/계단일 때만 |
| isAnchor | Boolean | `type == "anchor"` 여부 (서버가 계산) |
| x | Double | nullable |
| y | Double | nullable |

**Landmark**
| 필드 | 타입 | 비고 |
|---|---|---|
| id | String | PK |
| floorId | String | FK |
| name | String | |
| type | String | `room` \| `restroom` \| `facility` \| `entrance` |
| visualTagId | String | nullable |
| x | Double | nullable |
| y | Double | nullable |

**Admin** (로그인/회원가입/승인용)
| 필드 | 타입 | 비고 |
|---|---|---|
| id | String | PK |
| email | String | unique |
| password | String | **평문 저장 금지** — BCrypt로 해시해서 저장 (`spring-boot-starter-security`의 `PasswordEncoder`) |
| name | String | |
| org | String | 소속 기관 |
| position | String | nullable |
| phone | String | nullable |
| building | String | nullable, 담당 건물 |
| status | String | `pending` \| `active` \| `rejected` |
| role | String | `super_admin` \| `admin` |
| officialDocUrl | String | 공문 파일 업로드 후 저장 경로/URL |
| createdAt | LocalDateTime | `@CreationTimestamp` 로 자동 채움 |

> 참고: 프론트 코드(`AccountApprovalPage` 등)에는 아직 이 승인 관련 API 호출이 실제로 연결되어 있지 않습니다(화면만 있고 실제 fetch 호출은 없음). 회원가입/로그인(`/admin/auth/*`)은 확실히 필요하지만, 승인 목록/승인처리 API는 프론트가 붙기 전이라 엔드포인트 경로가 아직 확정되지 않았습니다 — 이 부분은 프론트 담당자와 경로를 먼저 맞추시는 걸 추천합니다.

---

## 5. 전체 API 명세 (엔드포인트 전부)

Base path: `/api` (Vite `.env`의 `VITE_API_BASE_URL` 기준). 아래 경로는 그 뒤에 붙는 부분입니다.

### 5-1. 인증

**`POST /admin/auth/login`**
- Request body: `{ email: string, password: string }`
- Response 200: `{ accessToken: string }`

**`POST /admin/auth/signup`**
- Request body: `{ email: string, password: string, name: string, org: string }`
- 참고: 기관 공문 파일(`officialDoc`)은 이 JSON과 별도로 `multipart/form-data`로 전송됨 — 파일 업로드용 별도 처리 필요
- Response: `201 Created`, 바디 없음

### 5-2. 건물 (Building)

**`GET /buildings`**
- Response 200: `Building[]`
  - `Building = { id, code, name, address?, floorCount?, favorite?, status? }`

**`POST /buildings`**
- Request body: `{ name: string, code: string, address?: string, floorCount?: number }`
- Response 201: `Building` (status는 서버가 `"floorplan_missing"`으로 초기화)

**`GET /buildings/:id`**
- Response 200: `Building`
- Response 404: 없으면

**`PATCH /buildings/:id`**
- Request body: `Partial<{ name, code, address, floorCount }>` (보낸 필드만 갱신)
- Response 200: `Building`

**`DELETE /buildings/:id`**
- Response 204, 바디 없음
- 부수효과: 해당 건물에 속한 층(Floor)도 함께 삭제 (mock 기준. cascade 삭제 구현 필요)

### 5-3. 층 (Floor)

**`GET /buildings/:id/floors`**
- Response 200: `Floor[]`
  - `Floor = { id, buildingId, floor, major, status? }`

**`POST /buildings/:id/floors`**
- Request body: `{ floor: number }`
- Response 201: `Floor` (서버가 `major = 100 + floor` 계산, `status = "floorplan_missing"` 초기화)

**`DELETE /buildings/:buildingId/floors/:floorId`**
- Response 204, 바디 없음

### 5-4. 수직 연결자 (Connector — 엘리베이터/계단)

**`GET /buildings/:id/connectors`**
- Response 200: `Connector[]`
  - `Connector = { id, buildingId, name, type: 'elevator'|'stairs', floors: number[] }`

**`POST /buildings/:id/connectors`**
- Request body: `{ name: string, type: 'elevator'|'stairs', floors: number[] }`
- Response 201: `Connector` (floors는 오름차순 정렬해서 저장)

**`DELETE /buildings/:buildingId/connectors/:connectorId`**
- Response 204, 바디 없음

### 5-5. 설계도 (Floorplan)

**`GET /floors/:floorId/floorplan`**
- Response 200: `Floorplan | null`
  - `Floorplan = { floorId, imageUrl, extracted: boolean }`

**`PUT /floors/:floorId/floorplan`**
- Request body: `{ imageUrl: string }` (업로드된 이미지의 data URL 또는 실제 URL)
- Response 200: `Floorplan` (`extracted: true`로 반환)
- **중요**: 실제 서버는 여기서 벽·이동영역을 자동 추출하는 처리를 해야 함 (이미지 분석 로직 — 지금은 우선 mock처럼 즉시 완료 처리해도 무방, 추후 고도화)
- 부수효과: 해당 층 status가 `floorplan_missing` → `review_needed`로 전환

**`DELETE /floors/:floorId/floorplan`**
- Response 204, 바디 없음

### 5-6. 이동영역 마스크 (검수 결과)

**`GET /floors/:floorId/mask`**
- Response 200: `FloorMask | null`
  - `FloorMask = { width: number, height: number, dataUrl: string }` (dataUrl은 채워진 영역을 나타내는 투명배경 PNG)

**`PUT /floors/:floorId/mask`**
- Request body: `FloorMask` (`{ width, height, dataUrl }`)
- Response 200: `{ ok: true }`
- 부수효과: 해당 층 status가 `review_needed` → `beacon_missing`으로 전환

### 5-7. 비콘 (Beacon)

**`GET /floors/:floorId/beacons`**
- Response 200: `Beacon[]`
  - `Beacon = { id, floorId, name, mac?, major, minor, type: 'anchor'|'checkpoint'|'connector', connectorId?, isAnchor, x?, y? }`

**`POST /floors/:floorId/beacons`**
- Request body: `{ name: string, mac?: string, minor: number, type: 'anchor'|'checkpoint'|'connector', connectorId?: string, x?: number, y?: number }`
- Response 201: `Beacon`
  - 서버가 계산하는 값: `major`(소속 층의 major), `isAnchor`(`type === 'anchor'`)
- 부수효과: 해당 층에 등록된 첫 비콘이면 status가 `beacon_missing` → `ready`로 전환

**`PATCH /floors/:floorId/beacons/:beaconId`**
- Request body: `Partial<{ name, mac, minor, type, connectorId, x, y }>`
- Response 200: `Beacon` (`isAnchor`는 `type` 변경 시 재계산)

**`DELETE /floors/:floorId/beacons/:beaconId`**
- Response 204, 바디 없음

### 5-8. 목적지 (Landmark)

**`GET /floors/:floorId/landmarks`**
- Response 200: `Landmark[]`
  - `Landmark = { id, floorId, name, type: 'room'|'restroom'|'facility'|'entrance', visualTagId?, x?, y? }`

**`POST /floors/:floorId/landmarks`**
- Request body: `{ name: string, type: 'room'|'restroom'|'facility'|'entrance', x?: number, y?: number }`
- Response 201: `Landmark`

**`PATCH /floors/:floorId/landmarks/:landmarkId`**
- Request body: `Partial<{ name, type, x, y }>`
- Response 200: `Landmark`

**`DELETE /floors/:floorId/landmarks/:landmarkId`**
- Response 204, 바디 없음

### 5-9. WebSocket (이미 구현됨, REST 아님)

**`/ws`**
- 클라이언트가 비콘 RSSI/IMU 원본 값을 JSON으로 전송하면, 서버가 중앙값 필터 + 칼만 필터 + 히스테리시스로 정제한 뒤 다른 연결된 세션들에 브로드캐스트
- REST API의 건물/층/비콘 데이터(특히 비콘의 `major`, `minor`, `x`, `y` 좌표, 그리고 설계도 이동영역 마스크)를 이 위치추정 로직이 참조하게 될 것이므로, DB에 저장된 비콘 좌표를 이 핸들러에서 조회할 수 있도록 나중에 `BeaconRepository`를 주입받는 구조로 확장하시면 됩니다.

---

## 6. 상태 전이(FloorSetupStatus) 요약

층 하나가 "안내 가능" 상태가 되기까지의 흐름이 여러 엔드포인트에 걸쳐 있어서 별도로 정리합니다. 각 액션 이후 서버가 이 값을 계산해서 내려줘야 합니다.

```
floorplan_missing  (층 생성 직후)
      ↓  PUT /floors/:id/floorplan
review_needed
      ↓  PUT /floors/:id/mask
beacon_missing
      ↓  POST /floors/:id/beacons (첫 등록)
ready
```

---

## 7. 다음 단계 제안

1. 위 Building 패턴을 참고해서 Floor, Connector, Floorplan, Mask, Beacon, Landmark 순서로 하나씩 구현 (의존관계상 이 순서가 자연스러움)
2. Admin 엔티티 + `spring-boot-starter-security` + JWT 라이브러리(`jjwt`)로 로그인/회원가입 구현
3. WebSocket 핸들러가 DB의 비콘 좌표를 참조하도록 연결
4. 설계도 자동 추출 로직은 우선 스텁으로 두고 나머지 기능부터 완성

원하시면 Floor나 Beacon 같은 특정 도메인 하나를 골라서 실제 코드를 같이 작성해드릴 수 있습니다.
