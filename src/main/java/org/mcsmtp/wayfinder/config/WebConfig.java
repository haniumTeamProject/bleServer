package org.mcsmtp.wayfinder.config;

import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

// CORS 설정은 Spring Security 도입 후 SecurityConfig의 CorsConfigurationSource로 옮겼습니다.
// Security 필터체인이 요청을 먼저 가로채기 때문에, 여기(MVC 레벨)에 CORS 매핑을
// 같이 두면 헤더가 중복으로 붙어 브라우저가 CORS 에러를 낼 수 있어 제거했습니다.
@Configuration
public class WebConfig implements WebMvcConfigurer {
}