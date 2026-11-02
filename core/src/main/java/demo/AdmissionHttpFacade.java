package demo;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Map;
import java.util.Set;

/** Framework-neutral admission/status mapping for the Spring MVC entrypoint.
 * Same laboratory bearer-token semantics and strict JSON input as the legacy adapter.
 * No JDBC, RabbitMQ, servlet or Spring classes occur here.
 */
public final class AdmissionHttpFacade {
  public record Reply(int status, String json, String location) {}
  private final RequestOperations requests;
  private final byte[] readToken, writeToken;
  private final IngressMeasurements metrics;
  public AdmissionHttpFacade(RequestOperations requests,String read,String write,boolean metricsEnabled){
    if(read==null||write==null||read.length()<24||write.length()<24||read.equals(write))
      throw new IllegalArgumentException("APP_READ_TOKEN and APP_WRITE_TOKEN must be distinct and >=24 chars");
    this.requests=requests;
    this.readToken=read.getBytes(StandardCharsets.UTF_8);
    this.writeToken=write.getBytes(StandardCharsets.UTF_8);
    this.metrics=metricsEnabled?new IngressMeasurements():null;
  }
  private boolean allowed(String auth,boolean writing){
    byte[] candidate=(auth!=null&&auth.startsWith("Bearer ")?auth.substring(7):"").getBytes(StandardCharsets.UTF_8);
    return MessageDigest.isEqual(writing?writeToken:readToken,candidate);
  }
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
    try{
      RequestOperations.Admission outcome=requests.create(input.get("externalId"),input.get("customerId"),input.get("productCode"));
      switch(outcome.status()){
        case CREATED: case REPLAY:
          code=outcome.status()==RequestOperations.AdmissionStatus.CREATED?202:200;
          return new Reply(code,"{\"id\":"+StrictJson.quote(outcome.id())+",\"idempotentReplay\":"+(code==200)+"}","/api/v1/requests/"+outcome.id());
        case CONFLICT: code=409;return err(code,"EXTERNAL_ID_CONFLICT");
        case UNCERTAIN: code=503;return err(code,"COMMIT_OUTCOME_UNKNOWN_RETRY_SAME_EXTERNAL_ID");
        default: code=503;return err(code,"ADMISSION_NOT_CONFIRMED");
      }
    }catch(RequestOperations.DataAccessUnavailable e){code=503;return err(code,"DATABASE_UNAVAILABLE_OR_OUTCOME_UNCERTAIN");}
     catch(Exception e){code=500;return err(code,"INTERNAL_ERROR");}
    finally{if(ticket!=null)metrics.finish(ticket,code);}
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
