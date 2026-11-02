package demo;

import java.net.URI;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.http.HttpMethod;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.oauth2.core.OAuth2Error;
import org.springframework.security.oauth2.core.OAuth2TokenValidator;
import org.springframework.security.oauth2.core.OAuth2TokenValidatorResult;
import org.springframework.security.oauth2.jwt.DelegatingOAuth2TokenValidator;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.security.oauth2.jwt.JwtValidators;
import org.springframework.security.oauth2.jwt.NimbusJwtDecoder;
import org.springframework.security.web.SecurityFilterChain;

/** Fail-closed, stateless JWT mode. The legacy static bearer-token mode is
 * deliberately laboratory-only and must be explicitly selected in deployment.
 */
@Configuration
public class SpringSecurityConfiguration {
  private static boolean jwtMode() {
    String mode=System.getenv().getOrDefault("APP_AUTH_MODE","jwt");
    if (!mode.equals("jwt") && !mode.equals("lab"))
      throw new IllegalArgumentException("APP_AUTH_MODE must be jwt or lab");
    return mode.equals("jwt");
  }
  private static String required(String name){
    String s=System.getenv(name);
    if(s==null||s.isBlank())throw new IllegalArgumentException(name+" is required for JWT mode");
    return s.trim();
  }
  private static URI trustedUri(String value,String label){
    URI uri=URI.create(value);
    boolean loopback=("127.0.0.1".equals(uri.getHost())||"localhost".equals(uri.getHost())||"[::1]".equals(uri.getHost()));
    if(uri.getUserInfo()!=null||uri.getFragment()!=null||uri.getHost()==null ||
        !("https".equals(uri.getScheme()) ||
         ("http".equals(uri.getScheme()) && loopback && "1".equals(System.getenv("APP_JWT_ALLOW_HTTP_LOOPBACK")))))
      throw new IllegalArgumentException(label+" must be HTTPS (HTTP loopback only for an explicit lab test)");
    return uri;
  }

  @Bean
  SecurityFilterChain deroyqueHttpSecurity(HttpSecurity http) throws Exception {
    http.csrf(csrf->csrf.disable());
    http.sessionManagement(session->session.sessionCreationPolicy(SessionCreationPolicy.STATELESS));
    if(jwtMode()) {
      // Do not let Spring's generated credentials or a fallback anonymous policy
      // turn a configuration failure into an open or partially protected service.
      http.authorizeHttpRequests(auth->auth
        .requestMatchers("/api/v1/health/liveness", "/api/v1/health/readiness").permitAll()
        .requestMatchers(HttpMethod.POST,"/api/v1/requests").hasAuthority("SCOPE_activation:write")
        .requestMatchers(HttpMethod.GET,"/api/v1/requests/*").hasAuthority("SCOPE_activation:read")
        .anyRequest().denyAll());
      http.oauth2ResourceServer(oauth->oauth.jwt(jwt->{}));
    } else {
      // LAB: AdmissionHttpFacade continues to enforce separate opaque read/write
      // bearer tokens exactly as before; NOT a production authentication mode.
      http.authorizeHttpRequests(auth->auth.anyRequest().permitAll());
    }
    return http.build();
  }

  @Bean
  @ConditionalOnProperty(name="app.auth.mode",havingValue="jwt")
  JwtDecoder deroyqueJwtDecoder(){
    String issuer=required("APP_JWT_ISSUER");
    String jwks=required("APP_JWT_JWKS_URI");
    String audience=required("APP_JWT_AUDIENCE");
    trustedUri(issuer,"APP_JWT_ISSUER");
    trustedUri(jwks,"APP_JWT_JWKS_URI");
    NimbusJwtDecoder decoder=NimbusJwtDecoder.withJwkSetUri(jwks).build();
    OAuth2TokenValidator<Jwt> aud=token-> token.getAudience()!=null && token.getAudience().contains(audience)
      ? OAuth2TokenValidatorResult.success()
      : OAuth2TokenValidatorResult.failure(new OAuth2Error("invalid_token","JWT audience mismatch",null));
    decoder.setJwtValidator(new DelegatingOAuth2TokenValidator<>(JwtValidators.createDefaultWithIssuer(issuer),aud));
    return decoder;
  }
}
