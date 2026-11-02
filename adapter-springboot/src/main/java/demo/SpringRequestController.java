package demo;

import jakarta.servlet.http.HttpServletRequest;
import java.io.IOException;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/** MVC transport only. Business, idempotency and lab auth are handled by the shared facade. */
@RestController
public class SpringRequestController {
  private final AdmissionHttpFacade facade;
  private final P13Service engine;
  public SpringRequestController(AdmissionHttpFacade facade,P13Service engine){this.facade=facade;this.engine=engine;}
  static ResponseEntity<String> response(AdmissionHttpFacade.Reply r){
    var b=ResponseEntity.status(r.status()).contentType(MediaType.APPLICATION_JSON)
       .header(HttpHeaders.CACHE_CONTROL,"no-store");
    if(r.location()!=null)b.header(HttpHeaders.LOCATION,r.location());
    return b.body(r.json());
  }
  @PostMapping(path="/api/v1/requests")
  public ResponseEntity<String> create(HttpServletRequest req,
     @RequestHeader(value="Authorization",required=false) String authorization,
     @RequestHeader(value="X-Deroyque-Load-Run",required=false) String run) throws IOException {
    // Read only 4097 bytes; a large HTTP request must not be buffered into a huge heap object.
    byte[] payload=req.getInputStream().readNBytes(4097);
    return response(facade.create(authorization,req.getContentType(),payload,run));
  }
  @GetMapping(path="/api/v1/requests/{id}")
  public ResponseEntity<String> find(@PathVariable("id") String id,
    @RequestHeader(value="Authorization",required=false) String authorization){
    return response(facade.find(authorization,id));
  }
  @GetMapping(path="/api/v1/lab/ingress-metrics")
  public ResponseEntity<String> ingress(@RequestParam(value="run",required=false) String run,
    @RequestHeader(value="Authorization",required=false) String authorization){
    return response(facade.measurements(authorization,run));
  }
  @GetMapping(path="/api/v1/health/liveness")
  public ResponseEntity<String> live(){return ResponseEntity.ok().contentType(MediaType.APPLICATION_JSON).body("{\"status\":\"UP\"}");}
  @GetMapping(path="/api/v1/health/readiness")
  public ResponseEntity<String> ready(){boolean ok=engine.isReady();return ResponseEntity.status(ok?200:503)
    .contentType(MediaType.APPLICATION_JSON).body("{\"status\":\""+(ok?"UP":"DOWN")+"\"}");}
}
