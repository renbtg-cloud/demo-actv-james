package demo;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.DependsOn;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationToken;

/** Spring Boot deployment option. Does not start the legacy JDK HTTP listener. */
@SpringBootApplication
public class SpringDeroyqueApplication {
  public static void main(String[] args) {SpringApplication.run(SpringDeroyqueApplication.class,args);}

  @Bean(initMethod="startEngine",destroyMethod="close")
  @DependsOn("flywayInitializer")
  P13Service asyncEngine(){return new P13Service();}

  @Bean
  RequestOperations requestOperations(P13Service engine){return engine.requestOperations();}

  @Bean
  AdmissionHttpFacade admissionHttpFacade(RequestOperations requests){
    boolean metrics="1".equals(System.getenv("APP_LAB_METRICS_ENABLED"));
    if ("jwt".equals(System.getenv().getOrDefault("APP_AUTH_MODE","jwt"))) {
      // Never trust raw bearer text here. The JwtAuthenticationToken is supplied
      // exclusively by Spring Security after signature, issuer, time and audience validation.
      return new AdmissionHttpFacade(requests,(ignored,writing)->{
        var authentication=SecurityContextHolder.getContext().getAuthentication();
        if (!(authentication instanceof JwtAuthenticationToken jwt) || !jwt.isAuthenticated()) return false;
        String authority=writing?"SCOPE_activation:write":"SCOPE_activation:read";
        return jwt.getAuthorities().stream().anyMatch(a->authority.equals(a.getAuthority()));
      },metrics);
    }
    return new AdmissionHttpFacade(requests,System.getenv("APP_READ_TOKEN"),System.getenv("APP_WRITE_TOKEN"),metrics);
  }
}
