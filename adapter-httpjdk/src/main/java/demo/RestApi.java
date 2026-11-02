package demo;

import com.sun.net.httpserver.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;

/** Experimental JSON/HTTP adapter. Binds to loopback, requires independent read/write bearer tokens.
 * NOT OAuth/JWT or production HTTP security. Spring Boot adapter remains future work.
 */
final class RestApi {
  private final RequestOperations requests;
  private final byte[] readToken,writeToken;
  private final IngressMeasurements labMetrics;
  RestApi(RequestOperations requests,String read,String write){this(requests,read,write,false);}
  RestApi(RequestOperations requests,String read,String write,boolean enableLabMetrics){
    if(read==null||write==null||read.length()<24||write.length()<24||read.equals(write))
      throw new IllegalArgumentException("APP_READ_TOKEN and APP_WRITE_TOKEN must be distinct and >=24 chars");
    this.requests=requests;readToken=read.getBytes(StandardCharsets.UTF_8);writeToken=write.getBytes(StandardCharsets.UTF_8);
    labMetrics=enableLabMetrics?new IngressMeasurements():null;
  }
  boolean allowed(HttpExchange ex,boolean writing){
    String auth=ex.getRequestHeaders().getFirst("Authorization");
    byte[] candidate=(auth!=null&&auth.startsWith("Bearer ")?auth.substring(7):"").getBytes(StandardCharsets.UTF_8);
    return MessageDigest.isEqual(writing?writeToken:readToken,candidate);
  }
  static void json(HttpExchange ex,int code,String value){
    try{
      ex.getResponseHeaders().set("Content-Type","application/json; charset=utf-8");
      ex.getResponseHeaders().set("Cache-Control","no-store");
      byte[] b=value.getBytes(StandardCharsets.UTF_8);
      ex.sendResponseHeaders(code,b.length);try(OutputStream out=ex.getResponseBody()){out.write(b);}
      ex.setAttribute("deroyque_http_status",code);
    }catch(IOException e){System.err.println("API_WRITE_FAILURE "+e.getClass().getSimpleName());}
  }
  static void err(HttpExchange ex,int code,String reason){json(ex,code,"{\"error\":"+StrictJson.quote(reason)+"}");}
  static boolean valid(String s,int max){return s!=null&&!s.isBlank()&&s.length()<=max&&s.chars().noneMatch(c->c<32||c==127);}
  void handle(HttpExchange ex){
    String path=ex.getRequestURI().getPath();String method=ex.getRequestMethod();
    boolean create=path.equals("/api/v1/requests");
    boolean metricsRoute=path.equals("/api/v1/lab/ingress-metrics");
    IngressMeasurements.Ticket ticket=null;
    boolean read=path.startsWith("/api/v1/requests/")&&!path.substring("/api/v1/requests/".length()).contains("/");
    try{
      if(metricsRoute){
        if(labMetrics==null){err(ex,404,"NOT_FOUND");return;}
        if(!method.equals("GET")){err(ex,405,"METHOD_NOT_ALLOWED");return;}
        if(!allowed(ex,false)){err(ex,401,"UNAUTHORIZED");return;}
        String query=ex.getRequestURI().getRawQuery();
        if(query==null||!query.startsWith("run=")||!IngressMeasurements.validRun(query.substring(4))){err(ex,400,"INVALID_RUN_ID");return;}
        json(ex,200,labMetrics.snapshot(query.substring(4)));return;
      }
      if(!create&&!read){err(ex,404,"NOT_FOUND");return;}
      if(create&&!method.equals("POST") || read&&!method.equals("GET")){err(ex,405,"METHOD_NOT_ALLOWED");return;}
      if(!allowed(ex,create)){err(ex,401,"UNAUTHORIZED");return;}
      if(create){
        String ctype=ex.getRequestHeaders().getFirst("Content-Type");
        if(ctype==null||!ctype.split(";",2)[0].trim().equalsIgnoreCase("application/json")){err(ex,415,"UNSUPPORTED_MEDIA_TYPE");return;}
        byte[] body=ex.getRequestBody().readNBytes(4097);
        if(body.length>4096){err(ex,413,"BODY_TOO_LARGE");return;}
        Map<String,String> input;
        try{input=StrictJson.parse(new String(body,StandardCharsets.UTF_8));}
        catch(IllegalArgumentException e){err(ex,400,"INVALID_JSON");return;}
        if(!input.keySet().equals(Set.of("externalId","customerId","productCode"))||
            !valid(input.get("externalId"),100)||!valid(input.get("customerId"),100)||!valid(input.get("productCode"),100)){
          err(ex,400,"INVALID_REQUEST");return;
        }
        if(labMetrics!=null){
          String run=ex.getRequestHeaders().getFirst("X-Deroyque-Load-Run");
          if(IngressMeasurements.validRun(run)&&input.get("externalId").startsWith(run+"-"))ticket=labMetrics.begin(run);
        }
        var result=requests.create(input.get("externalId"),input.get("customerId"),input.get("productCode"));
        switch(result.status()){
          case CREATED,REPLAY->{
            int status=result.status()==RequestOperations.AdmissionStatus.CREATED?202:200;
            ex.getResponseHeaders().set("Location","/api/v1/requests/"+result.id());
            json(ex,status,"{\"id\":"+StrictJson.quote(result.id())+",\"idempotentReplay\":"+(status==200)+"}");
          }
          case CONFLICT->err(ex,409,"EXTERNAL_ID_CONFLICT");
          case UNCERTAIN->err(ex,503,"COMMIT_OUTCOME_UNKNOWN_RETRY_SAME_EXTERNAL_ID");
          default->err(ex,503,"ADMISSION_NOT_CONFIRMED");
        }
      }else{
        String id=path.substring("/api/v1/requests/".length());
        if(!valid(id,40)){err(ex,400,"INVALID_ID");return;}
        var d=requests.find(id);
        if(d==null){err(ex,404,"NOT_FOUND");return;}
        json(ex,200,"{\"id\":"+StrictJson.quote(d.id())+
          ",\"externalId\":"+StrictJson.quote(d.externalId())+
          ",\"customerId\":"+StrictJson.quote(d.customerId())+
          ",\"productCode\":"+StrictJson.quote(d.productCode())+
          ",\"status\":"+StrictJson.quote(d.status())+
          ",\"attempts\":"+d.attempts()+",\"version\":"+d.version()+
          ",\"evidenceIncidents\":"+d.incidentCount()+
          ",\"signedEvidenceCount\":"+d.signedEvidenceCount()+"}");
      }
    }catch(RequestOperations.DataAccessUnavailable ex1){
      System.err.println("API_STORAGE_ERROR "+ex1.getClass().getSimpleName());
      err(ex,503,"DATABASE_UNAVAILABLE_OR_OUTCOME_UNCERTAIN");
    }catch(Exception e){System.err.println("API_FAILURE "+e.getClass().getSimpleName());err(ex,500,"INTERNAL_ERROR");}
    finally{if(labMetrics!=null&&ticket!=null){Object code=ex.getAttribute("deroyque_http_status");labMetrics.finish(ticket,code instanceof Integer ? (Integer)code : 0);}}
  }
}
