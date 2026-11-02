package demo;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.Semaphore;
import java.util.function.BiPredicate;
import java.util.concurrent.TimeUnit;

/** Framework-neutral admission/status mapping for the Spring MVC entrypoint.
 * Same laboratory bearer-token semantics and strict JSON input as the legacy adapter.
 * No JDBC, RabbitMQ, servlet or Spring classes occur here.
 */
public final class AdmissionHttpFacade {
  public record Reply(int status, String json, String location) {}
  private final RequestOperations requests;
  private final BiPredicate<String, Boolean> access;
  private final IngressMeasurements metrics;
  /** Bounded admission: this JVM cannot create unbounded simultaneous JDBC connections
   * from HTTP requests. Unlike monitoring, admission backpressure is part of normal safety.
   * The default is deliberately below PostgreSQL's common 100-connection limit, leaving
   * capacity for the engine, watchdog, outbox and sibling JVMs. This is a per-instance
   * limit, NOT a distributed connection pool or a claim of global capacity.
   */
  private final Semaphore admissionSlots;
  private final long admissionWaitMs;
  private static int configInt(String name,int fallback,int min,int max){
    String raw=System.getenv(name);
    int n=raw==null||raw.isBlank()?fallback:Integer.parseInt(raw);
    if(n<min||n>max)throw new IllegalArgumentException(name+" must be "+min+".."+max);
    return n;
  }
  private static void diagnoseUnavailable(RequestOperations.DataAccessUnavailable failure){
    if(!"1".equals(System.getenv("APP_LAB_DIAGNOSTICS_ENABLED")))return;
    // Core has no JDBC dependency and emits only a validated 5-character SQLState.
    String code=failure.diagnosticCode();
    if(code.matches("[A-Z0-9]{5}"))
      System.err.println("DEROYQUE_ADMISSION_SQLSTATE "+code);
  }
  public AdmissionHttpFacade(RequestOperations requests,String read,String write,boolean metricsEnabled){
    this(requests,labAccess(read,write),metricsEnabled);
  }
  private static BiPredicate<String,Boolean> labAccess(String read,String write){
    if(read==null||write==null||read.length()<24||write.length()<24||read.equals(write))
      throw new IllegalArgumentException("APP_READ_TOKEN and APP_WRITE_TOKEN must be distinct and >=24 chars");
    byte[] readBytes=read.getBytes(StandardCharsets.UTF_8),writeBytes=write.getBytes(StandardCharsets.UTF_8);
    return (authorization,writing)->{
      byte[] candidate=(authorization!=null&&authorization.startsWith("Bearer ")?authorization.substring(7):"").getBytes(StandardCharsets.UTF_8);
      return MessageDigest.isEqual(writing?writeBytes:readBytes,candidate);
    };
  }
  /** Authentication is injected by the framework boundary. The neutral core never
   * parses JWTs or trusts unverified claims. This constructor is for a verified
   * authorization policy, NOT a replacement for the Spring Security filter chain.
   */
  public AdmissionHttpFacade(RequestOperations requests,BiPredicate<String,Boolean> verifiedAccess,boolean metricsEnabled){
    this.requests=java.util.Objects.requireNonNull(requests);
    this.access=java.util.Objects.requireNonNull(verifiedAccess);
    this.metrics=metricsEnabled?new IngressMeasurements():null;
    this.admissionSlots=new Semaphore(configInt("APP_ADMISSION_MAX_IN_FLIGHT",32,1,256),true);
    this.admissionWaitMs=configInt("APP_ADMISSION_WAIT_MS",8000,1,30000);
  }
  private boolean allowed(String auth,boolean writing){return access.test(auth,writing);}

  private static Reply err(int status,String message){return new Reply(status,"{\"error\":"+StrictJson.quote(message)+"}",null);}
  static boolean valid(String s,int max){return s!=null&&!s.isBlank()&&s.length()<=max&&s.chars().noneMatch(c->c<32||c==127);}
  public Reply create(String authorization,String contentType,byte[] body,String run){
    if(!allowed(authorization,true))return err(401,"UNAUTHORIZED");
    if(contentType==null||!contentType.split(";",2)[0].trim().equalsIgnoreCase("application/json"))return err(415,"UNSUPPORTED_MEDIA_TYPE");
    if(body==null)return err(400,"INVALID_JSON");
    if(body.length>4096)return err(413,"BODY_TOO_LARGE");
    Map<String,String> input;
    try {input=StrictJson.parse(new String(body,StandardCharsets.UTF_8));}
    catch(IllegalArgumentException ex){return err(400,"INVALID_JSON");}
    if(!input.keySet().equals(Set.of("externalId","customerId","productCode"))||
      !valid(input.get("externalId"),100)||!valid(input.get("customerId"),100)||!valid(input.get("productCode"),100))
      return err(400,"INVALID_REQUEST");
    IngressMeasurements.Ticket ticket=null;
    if(metrics!=null&&IngressMeasurements.validRun(run)&&input.get("externalId").startsWith(run+"-"))ticket=metrics.begin(run);
    int code=0;
    boolean admitted=false;
    try{
      try {admitted=admissionSlots.tryAcquire(admissionWaitMs,TimeUnit.MILLISECONDS);}
      catch(InterruptedException interrupted){
        Thread.currentThread().interrupt();code=503;return err(code,"ADMISSION_INTERRUPTED_RETRY_SAME_EXTERNAL_ID");
      }
      if(!admitted){code=503;return err(code,"ADMISSION_BUSY_RETRY_SAME_EXTERNAL_ID");}
      RequestOperations.Admission outcome=requests.create(input.get("externalId"),input.get("customerId"),input.get("productCode"));
      switch(outcome.status()){
        case CREATED: case REPLAY:
          code=outcome.status()==RequestOperations.AdmissionStatus.CREATED?202:200;
          return new Reply(code,"{\"id\":"+StrictJson.quote(outcome.id())+",\"idempotentReplay\":"+(code==200)+"}","/api/v1/requests/"+outcome.id());
        case CONFLICT: code=409;return err(code,"EXTERNAL_ID_CONFLICT");
        case UNCERTAIN: code=503;return err(code,"COMMIT_OUTCOME_UNKNOWN_RETRY_SAME_EXTERNAL_ID");
        default: code=503;return err(code,"ADMISSION_NOT_CONFIRMED");
      }
    }catch(RequestOperations.DataAccessUnavailable e){
      diagnoseUnavailable(e);code=503;return err(code,"DATABASE_UNAVAILABLE_OR_OUTCOME_UNCERTAIN");
    }
     catch(Exception e){code=500;return err(code,"INTERNAL_ERROR");}
    finally{
      if(admitted)admissionSlots.release();
      if(ticket!=null)metrics.finish(ticket,code);
    }
  }
  public Reply find(String authorization,String id){
    if(!allowed(authorization,false))return err(401,"UNAUTHORIZED");
    if(!valid(id,40))return err(400,"INVALID_ID");
    try{
      RequestOperations.Detail d=requests.find(id);
      if(d==null)return err(404,"NOT_FOUND");
      return new Reply(200,"{\"id\":"+StrictJson.quote(d.id())+
          ",\"externalId\":"+StrictJson.quote(d.externalId())+
          ",\"customerId\":"+StrictJson.quote(d.customerId())+
          ",\"productCode\":"+StrictJson.quote(d.productCode())+
          ",\"status\":"+StrictJson.quote(d.status())+
          ",\"attempts\":"+d.attempts()+",\"version\":"+d.version()+
          ",\"evidenceIncidents\":"+d.incidentCount()+
          ",\"signedEvidenceCount\":"+d.signedEvidenceCount()+"}",null);
    }catch(RequestOperations.DataAccessUnavailable e){return err(503,"DATABASE_UNAVAILABLE_OR_OUTCOME_UNCERTAIN");}
     catch(Exception e){return err(500,"INTERNAL_ERROR");}
  }
  public Reply measurements(String authorization,String run){
    if(metrics==null)return err(404,"NOT_FOUND");
    if(!allowed(authorization,false))return err(401,"UNAUTHORIZED");
    if(!IngressMeasurements.validRun(run))return err(400,"INVALID_RUN_ID");
    return new Reply(200,metrics.snapshot(run),null);
  }
}
