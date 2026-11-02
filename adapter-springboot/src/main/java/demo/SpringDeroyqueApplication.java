package demo;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.DependsOn;

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
    return new AdmissionHttpFacade(requests,System.getenv("APP_READ_TOKEN"),System.getenv("APP_WRITE_TOKEN"),
      "1".equals(System.getenv("APP_LAB_METRICS_ENABLED")));
  }
}
