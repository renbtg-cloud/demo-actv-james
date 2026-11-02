package demo;

import java.net.URI;
import java.time.Instant;
import java.time.Duration;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.http.HttpMethod;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.oauth2.core.OAuth2Error;
import org.springframework.security.oauth2.core.OAuth2TokenValidator;
import org.springframework.security.oauth2.core.OAuth2TokenValidatorResult;
import org.springframework.security.oauth2.core.DelegatingOAuth2TokenValidator;
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

  private static long maximumTokenLifetimeSeconds(){
    String raw=System.getenv().getOrDefault("APP_JWT_MAX_LIFETIME_SECONDS","3600");
    try {
      long seconds=Long.parseLong(raw);
      if(seconds<60 || seconds>86400)
        throw new IllegalArgumentException("APP_JWT_MAX_LIFETIME_SECONDS must be between 60 and 86400");
      return seconds;
    } catch(NumberFormatException invalid){
      throw new IllegalArgumentException("APP_JWT_MAX_LIFETIME_SECONDS must be a valid integer",invalid);
    }
  }

  /** Access-token profile: no timeless, future-issued, or excessive-lifetime JWTs.
   * Nimbus already checks the RS256 signature; default validators check issuer,
   * expiration and not-before with their usual skew allowance.
   */
  static OAuth2TokenValidator<Jwt> accessTokenProfile(long maxLifetimeSeconds){
    return token->{
      Instant issued=token.getIssuedAt();
      Instant expires=token.getExpiresAt();
      String subject=token.getSubject();
      if(issued==null || expires==null || subject==null || subject.isBlank())
        return OAuth2TokenValidatorResult.failure(new OAuth2Error("invalid_token","JWT requires iat, exp and sub",null));
      Instant now=Instant.now();
      if(issued.isAfter(now.plusSeconds(60)) || !expires.isAfter(issued) ||
         Duration.between(issued,expires).compareTo(Duration.ofSeconds(maxLifetimeSeconds))>0 ||
         (token.getNotBefore()!=null && token.getNotBefore().isAfter(expires)))
        return OAuth2TokenValidatorResult.failure(new OAuth2Error("invalid_token","JWT time claim policy mismatch",null));
      return OAuth2TokenValidatorResult.success();
    };
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
    decoder.setJwtValidator(new DelegatingOAuth2TokenValidator<>(JwtValidators.createDefaultWithIssuer(issuer),aud,accessTokenProfile(maximumTokenLifetimeSeconds())));
    return decoder;
  }
}
